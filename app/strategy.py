"""India-first discovery, regional relevance, and editorial workflow guards."""
from __future__ import annotations
import hashlib
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

TREND_ENDPOINT = 'https://trends.google.com/trending/rss?geo={region}'
NEWS_ENDPOINT = 'https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en'
TECH = re.compile(r'\b(ai|artificial intelligence|chatgpt|gemini|openai|google|microsoft|apple|iphone|android|laptop|nvidia|software|chip|robot|app|internet|technology|tesla|spacex|phone|startup|cyber|quantum|samsung|pixel)\b',re.I)
INDIA = re.compile(r'\b(india|indian|bharat|delhi|mumbai|bengaluru|bangalore|chennai|hyderabad|bollywood|ipl|bcci|rupee|inr|isro|jio|airtel|cricket|virat|kohli|rohit|sharma|mahendra|modi|cbse|neet|upsc)\b',re.I)
SENSITIVE = re.compile(r'\b(death|killed|murder|suicide|rape|war|attack|election|minister|fraud|scam|disease|medicine|stock|shares|arrest|terror|bomb|religion|riot|disaster|earthquake|finance|politics)\b',re.I)

@dataclass(frozen=True)
class Settings:
    language: str = 'hinglish'
    shorts_per_day: int = 3
    scope: str = 'india-global'
    india_weight: float = .65
    global_weight: float = .35
    slots_ist: tuple[str,...] = ('09:00','14:00','20:00')

SETTINGS=Settings()


def classify(title: str) -> str:
    if TECH.search(title): return 'tech'
    if INDIA.search(title): return 'india'
    return 'global'


def estimate_volume(traffic: str) -> int:
    match=re.search(r'(\d+(?:[.,]\d+)?)\s*([KMB]?)',traffic.upper().replace(',',''))
    if not match: return 0
    return int(float(match.group(1))*{'':1,'K':1000,'M':1000000,'B':1000000000}[match.group(2)])


def rank_topic(title: str,region: str,traffic: str='',source: str='google_trends') -> tuple[int,dict]:
    """Explainable heuristic ranking, NOT a predictive virality probability."""
    volume=estimate_volume(traffic)
    momentum=min(100,round(20+20*math.log10(max(volume,1))/2)) if volume else 30
    region_fit=95 if region=='IN' else (78 if INDIA.search(title) else (65 if TECH.search(title) else 38))
    freshness=75 if source=='google_trends' else 50
    originality=70 if classify(title)=='tech' else 55
    monetization=75 if classify(title)=='tech' else 45
    score=round(.30*momentum+.25*region_fit+.20*freshness+.15*originality+.10*monetization)
    if SENSITIVE.search(title): score=min(60,score)
    return min(score,100),{'trend_momentum':momentum,'india_relevance':region_fit,'freshness':freshness,'originality':originality,'monetization_fit':monetization,'method':'heuristic_v0.2','sensitive':bool(SENSITIVE.search(title))}


def item_id(title: str) -> str:
    # Cross-region deduplication: the same title in India/US/UK is ONE topic.
    normalized=re.sub(r'[^\w]+',' ',title.casefold()).strip()
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:20]


def parse_trends_xml(content:bytes,region:str)->list[dict]:
    root=ET.fromstring(content)
    ns={'ht':'https://trends.google.com/trending/rss'}
    out=[]
    for item in root.findall('./channel/item'):
        title=(item.findtext('title') or '').strip()
        if not title: continue
        traffic=(item.findtext('ht:approx_traffic',default='',namespaces=ns) or '').strip()
        links=[]
        for child in item.findall('ht:news_item',ns):
            link=(child.findtext('ht:news_item_url',default='',namespaces=ns) or '').strip()
            if link.startswith('https://'): links.append(link)
        score,features=rank_topic(title,region,traffic)
        out.append(dict(id=item_id(title),title=title,region=region,traffic=traffic,
                        sources=links[:5],score=score,features=features,adapter='google_trends',
                        category=classify(title)))
    return out


def parse_news_xml(content:bytes,query:str)->list[dict]:
    root=ET.fromstring(content)
    out=[]
    for item in root.findall('./channel/item'):
        title=(item.findtext('title') or '').strip()
        url=(item.findtext('link') or '').strip()
        if not title or not url.startswith('https://'): continue
        # Google News headline and link are *discovery leads*, not corroboration.
        score,features=rank_topic(title,'IN',source='google_news')
        out.append(dict(id=item_id(title),title=title,region='IN',traffic='',sources=[url],
                        score=score,features=features,adapter='google_news',category=classify(title)))
    return out


async def collect(regions=('IN','US','GB'),news_queries=('technology India','AI technology')) -> tuple[list[dict],dict]:
    items=[];errors={}
    async with httpx.AsyncClient(timeout=18,follow_redirects=True,headers={'User-Agent':'ShortsPilot/0.2 (+local editorial tool)'}) as client:
        for region in regions:
            if region not in ('IN','US','GB'):
                errors[f'trends:{region}']='Unsupported region';continue
            try:
                res=await client.get(TREND_ENDPOINT.format(region=region));res.raise_for_status()
                items+=parse_trends_xml(res.content,region)
            except (httpx.HTTPError,ET.ParseError) as exc:errors[f'trends:{region}']=str(exc)
        for query in news_queries:
            try:
                res=await client.get(NEWS_ENDPOINT.format(query=quote(query)));res.raise_for_status()
                items+=parse_news_xml(res.content,query)[:30]
            except (httpx.HTTPError,ET.ParseError) as exc:errors[f'news:{query}']=str(exc)
    return items,errors
