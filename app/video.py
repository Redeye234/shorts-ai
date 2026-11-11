"""Original vertical cards with Hinglish narration (Edge TTS optional)."""
from __future__ import annotations
import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from . import core

DEFAULT_VOICE='hi-IN-SwaraNeural'

def font(size):
    for p in ['/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
              'C:/Windows/Fonts/Nirmala.ttf','C:/Windows/Fonts/arial.ttf']:
        if Path(p).exists():return ImageFont.truetype(p,size)
    return ImageFont.load_default()

def wrap_words(draw,words,ft,maxwidth):
    lines=[];line=''
    for word in words.split():
        trial=(line+' '+word).strip()
        if draw.textbbox((0,0),trial,font=ft)[2]>maxwidth and line:
            lines.append(line);line=word
        else:line=trial
    if line:lines.append(line)
    return lines

def card(title:str,text:str,path:Path,index:int,total:int,category:str='global'):
    im=Image.new('RGB',(1080,1920),(11,19,36));draw=ImageDraw.Draw(im)
    accent={'tech':(40,146,239),'india':(24,181,153),'global':(224,148,55)}.get(category,(40,146,239))
    draw.rounded_rectangle((65,82,1015,170),radius=20,fill=accent)
    draw.text((100,105),'SHORTSPILOT  /  ORIGINAL EXPLAINER',font=font(32),fill='white')
    y=275
    for line in wrap_words(draw,title,font(67),900)[:5]:
        draw.text((80,y),line,font=font(67),fill=(248,250,252));y+=93
    draw.rounded_rectangle((80,y+35,1000,y+46),radius=5,fill=accent);y+=165
    for line in wrap_words(draw,text,font(53),910)[:12]:
        draw.text((80,y),line,font=font(53),fill=(236,241,248));y+=82
    draw.text((80,1790),f'{index} / {total}   •   Independent commentary',font=font(30),fill=(164,176,193))
    im.save(path)

def probe_seconds(path:Path)->float:
    result=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],
                          capture_output=True,text=True,check=True,timeout=20)
    return float(result.stdout.strip())

async def synthesize(script:str,path:Path,voice:str=DEFAULT_VOICE):
    try:import edge_tts
    except ImportError as exc:raise RuntimeError('Install optional voice dependency: pip install edge-tts') from exc
    # Uses an external voice service; requires network and permission to use service.
    await edge_tts.Communicate(script,voice=voice).save(str(path))
    if not path.is_file() or path.stat().st_size<500:raise RuntimeError('Voice generation returned no audio')

async def render(topic:dict,with_voice:bool=True):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise RuntimeError('Install FFmpeg and FFprobe in PATH')
    if topic['status'] not in ('drafted','rendered'):
        raise ValueError('Draft must be completed before rendering')
    script=topic['script'].strip()
    if len(script)<60:raise ValueError('Script needs at least 60 characters')
    folder=core.OUT/topic['id'];folder.mkdir(parents=True,exist_ok=True)
    audio=folder/'narration.mp3'
    if with_voice:
        await synthesize(script,audio)
    elif audio.exists():audio.unlink()  # previews must never inherit stale narration
    words=script.split()
    chunks=[' '.join(words[i:i+15]) for i in range(0,len(words),15)][:12]
    if len(chunks)<2:raise ValueError('Script too short for storyboard')
    total_duration=probe_seconds(audio) if with_voice else min(55,max(12,len(words)/2.5))
    scene_duration=max(1.3,total_duration/len(chunks))
    # Render one scene per short block of spoken text; timed by estimated word length.
    concat=[]
    for index,chunk in enumerate(chunks,1):
        filename=f'frame_{index:02}.png'
        card(topic['title'],chunk,folder/filename,index,len(chunks),topic.get('category','global'))
        concat+=[f"file '{filename}'",f'duration {scene_duration:.3f}']
    concat.append(f"file 'frame_{len(chunks):02}.png'")
    (folder/'scenes.txt').write_text('\n'.join(concat),encoding='utf-8')
    output=folder/('short.mp4' if with_voice else 'preview_silent.mp4')
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(folder/'scenes.txt')]
    if with_voice:cmd+=['-i',str(audio)]
    else:cmd+=['-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=44100']
    cmd+=['-t',f'{total_duration:.3f}','-r','30','-vf','fps=30,format=yuv420p',
          '-c:v','libx264','-preset','veryfast','-crf','25','-pix_fmt','yuv420p','-c:a','aac','-b:a','128k',
          '-movflags','+faststart',str(output)]
    subprocess.run(cmd,check=True,cwd=folder,capture_output=True,text=True,timeout=300)
    updated=core.update_topic(topic['id'],status='rendered',output_file=str(output),voice_file=str(audio) if with_voice else '')
    return updated
