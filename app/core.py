"""Local SQLite trend queue, permitted RSS fetches, script drafting and video rendering."""
import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import textwrap
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(os.getenv('SHORTSPILOT_HOME', Path(__file__).resolve().parent.parent))
ROOT.mkdir(parents=True, exist_ok=True)
OUT = ROOT / 'output'
OUT.mkdir(exist_ok=True)
DB = ROOT / 'shortspilot.db'
FEEDS = {
    'IN': 'https://trends.google.com/trending/rss?geo=IN',
    'US': 'https://trends.google.com/trending/rss?geo=US',
    'GB': 'https://trends.google.com/trending/rss?geo=GB',
}
# Conservative auto-hold: editorial review required for these topics.
SENSITIVE = re.compile(r'\b(death|killed|murder|suicide|rape|war|attack|election|minister|fraud|scam|disease|medicine|stock|shares|arrest|terror|bomb|religion|riot)\b', re.I)
TECH = re.compile(r'\b(ai|chatgpt|gemini|openai|google|microsoft|apple|iphone|android|laptop|nvidia|software|chip|robot|app|internet|technology|tesla|spacex|phone|launch|startup|cyber|quantum)\b', re.I)


def connection():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS topics (
      id TEXT PRIMARY KEY, title TEXT NOT NULL, region TEXT NOT NULL,
      traffic TEXT, discovered_at TEXT NOT NULL, sources_json TEXT NOT NULL,
      score INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'discovered',
      editor_notes TEXT NOT NULL DEFAULT '', script TEXT NOT NULL DEFAULT '',
      output_file TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT ''
    )''')
    c.commit()
    return c


def classify(title):
    return 'tech' if TECH.search(title) else 'general'


def rank(title, region, traffic):
    # Heuristic prioritization, NOT real viewer interest or predicted virality.
    digits = re.findall(r'\d+', traffic.replace(',', ''))
    volume = int(digits[0]) if digits else 0
    if 'M' in traffic.upper(): volume *= 1_000_000
    elif 'K' in traffic.upper(): volume *= 1_000
    return min(100, 30 + (28 if classify(title) == 'tech' else 0) + (12 if region == 'IN' else 5) + min(30, volume // 3000))


def parse_rss(data: bytes, region: str):
    root = ET.fromstring(data)
    ns = {'ht':'https://trends.google.com/trending/rss'}
    result = []
    for item in root.findall('./channel/item'):
        title = (item.findtext('title') or '').strip()
        if not title: continue
        traffic = (item.findtext('ht:approx_traffic', default='', namespaces=ns) or '').strip()
        links = []
        for story in item.findall('ht:news_item', ns):
            link = (story.findtext('ht:news_item_url', default='', namespaces=ns) or '').strip()
            if link.startswith('https://'): links.append(link)
        if not links:
            links = ['https://trends.google.com/trending?geo='+quote(region)]
        identifier = hashlib.sha256((region+':'+title.casefold()).encode()).hexdigest()[:16]
        result.append({'id':identifier, 'title':title, 'region':region,
                       'traffic':traffic, 'sources':links[:4],
                       'score':rank(title, region, traffic)})
    return result


async def fetch_trends(regions=('IN','US','GB')):
    count = 0
    errors = {}
    async with httpx.AsyncClient(timeout=20, headers={'User-Agent':'ShortsPilotMVP/0.1 (personal RSS reader)'}, follow_redirects=True) as client:
        for region in regions:
            if region not in FEEDS:
                errors[region] = 'Unsupported region'; continue
            try:
                r = await client.get(FEEDS[region]); r.raise_for_status()
                items = parse_rss(r.content, region)
                with connection() as c:
                    for row in items:
                        before = c.total_changes
                        status = 'needs_review' if SENSITIVE.search(row['title']) else 'discovered'
                        c.execute('''INSERT OR IGNORE INTO topics
                         (id,title,region,traffic,discovered_at,sources_json,score,status)
                         VALUES(?,?,?,?,?,?,?,?)''',
                         (row['id'],row['title'],region,row['traffic'],datetime.now(timezone.utc).isoformat(),
                          json.dumps(row['sources']),row['score'],status))
                        count += c.total_changes - before
                    c.commit()
            except (httpx.HTTPError, ET.ParseError) as exc:
                errors[region] = str(exc)
    return {'added':count,'errors':errors}


def list_topics():
    with connection() as c:
        rows = c.execute('SELECT * FROM topics ORDER BY score DESC, discovered_at DESC LIMIT 200').fetchall()
    out = []
    for r in rows:
        d = dict(r); d['sources'] = json.loads(d.pop('sources_json')); d['features'] = json.loads(d.pop('features_json','{}')); d['regions'] = json.loads(d.pop('regions_json','[]')); out.append(d)
    return out


def get_topic(id):
    with connection() as c:
        r = c.execute('SELECT * FROM topics WHERE id=?',(id,)).fetchone()
    if not r: raise KeyError(id)
    d = dict(r); d['sources'] = json.loads(d.pop('sources_json')); d['features'] = json.loads(d.pop('features_json','{}')); d['regions'] = json.loads(d.pop('regions_json','[]')); return d


def update_topic(id, **fields):
    allowed = {'status','editor_notes','script','output_file','error','voice_file'}
    if not fields or not fields.keys() <= allowed: raise ValueError('Invalid fields')
    if fields.get('status') not in (None, 'discovered','needs_review','drafted','rendered','approved','rejected','queued','uploaded_private'):
        raise ValueError('Invalid status')
    with connection() as c:
        query = 'UPDATE topics SET '+', '.join(f'{k}=?' for k in fields)+' WHERE id=?'
        result=c.execute(query,(*fields.values(),id)); c.commit()
        if not result.rowcount: raise KeyError(id)
    return get_topic(id)


async def local_draft(topic):
    """Optional Ollama, deliberately constrained to editor-confirmed facts."""
    notes = topic['editor_notes'].strip()
    if len(notes) < 50:
        raise ValueError('Add at least 50 characters of fact-checked editorial notes before drafting.')
    model = os.getenv('OLLAMA_MODEL','qwen2.5:7b')
    prompt = ("Write a compelling 70-110 word Hindi/Hinglish YouTube Short script. "
              "Use ONLY facts explicitly in the editor notes. Do not invent numbers, events, quotations or claims. "
              "Avoid misleading sensationalism. Plain spoken narration, no stage directions. "
              f"Topic: {topic['title']}\nVerified editor notes: {notes}")
    async with httpx.AsyncClient(timeout=100) as client:
        resp=await client.post(os.getenv('OLLAMA_URL','http://127.0.0.1:11434')+'/api/generate',
            json={'model':model,'prompt':prompt,'stream':False})
        resp.raise_for_status()
        script=resp.json()['response'].strip()
    return update_topic(topic['id'],script=script,status='drafted')


def font(size):
    options = [
      '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
      'C:/Windows/Fonts/arialbd.ttf',
    ]
    for path in options:
        if Path(path).exists(): return ImageFont.truetype(path,size)
    return ImageFont.load_default()


def render_card(title, body, path, index, total):
    w,h=1080,1920
    im=Image.new('RGB',(w,h),(13,19,37)); dr=ImageDraw.Draw(im)
    dr.rounded_rectangle((70,100,1010,180),radius=25,fill=(29,112,190))
    dr.text((110,119),'SHORTSPILOT  |  ORIGINAL EXPLAINER',font=font(33),fill='white')
    def lines(text, width=25):
        return textwrap.wrap(text, width=width, break_long_words=False, replace_whitespace=False)
    y=290
    for line in lines(title,22)[:5]:
        dr.text((85,y),line,font=font(65),fill=(239,246,255)); y+=90
    dr.rectangle((85,y+30,995,y+35), fill=(0,201,169)); y+=130
    for line in lines(body,33)[:12]:
        dr.text((85,y),line,font=font(43),fill=(232,234,245)); y+=75
    dr.text((85,1770),f'SCENE {index}/{total}  |  Verify facts before publishing',font=font(28),fill=(136,154,181))
    im.save(path)


def render_video(topic):
    if not shutil.which('ffmpeg'): raise RuntimeError('FFmpeg not found in PATH')
    script = topic['script'].strip()
    if len(script) < 60: raise ValueError('Script must be at least 60 characters')
    if topic['status'] not in ('drafted','rendered','approved'):
        raise ValueError('Topic must be drafted first')
    folder=OUT/topic['id']; folder.mkdir(exist_ok=True)
    words=script.split()
    chunks=[' '.join(words[i:i+17]) for i in range(0,len(words),17)]
    chunks=chunks[:9]
    if not chunks: raise ValueError('Empty script')
    concat=[]
    for i,chunk in enumerate(chunks,1):
        img=folder/f'scene_{i:02}.png'
        render_card(topic['title'],chunk,img,i,len(chunks))
        duration=max(3.0,min(8.0,len(chunk.split())/2.5))
        concat.extend([f"file '{img.name}'",f'duration {duration:.2f}'])
    concat.append(f"file '{img.name}'")
    (folder/'scenes.txt').write_text('\n'.join(concat),encoding='utf-8')
    video=folder/'short.mp4'
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(folder/'scenes.txt'),
         '-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=44100',
         '-c:v','libx264','-preset','ultrafast','-crf','27','-pix_fmt','yuv420p',
         '-c:a','aac','-b:a','96k','-shortest','-movflags','+faststart',str(video)]
    subprocess.run(cmd,cwd=folder,check=True,capture_output=True,text=True,timeout=180)
    return update_topic(topic['id'],status='rendered',output_file=str(video))
