"""ShortsPilot 0.2: local review queue, India-first trend ingestion and voice previews."""
from __future__ import annotations
import os
from pathlib import Path
from fastapi import FastAPI,HTTPException,Header,Response
from fastapi.responses import HTMLResponse,FileResponse
from pydantic import BaseModel
from . import core,strategy,workflow,video

app=FastAPI(title='ShortsPilot AI',version='0.2.0')
workflow.migrate()

class Edit(BaseModel):
    editor_notes:str|None=None
    script:str|None=None

class Approval(BaseModel):
    action:str
    actor:str='owner'

def auth(token):
    secret=os.getenv('SHORTSPILOT_ADMIN_TOKEN','')
    if not secret or token!=secret:raise HTTPException(401,'Set SHORTSPILOT_ADMIN_TOKEN and pass X-Admin-Token')

def fail(exc):
    if isinstance(exc,KeyError):raise HTTPException(404,'Topic not found')
    if isinstance(exc,ValueError):raise HTTPException(400,str(exc))
    raise HTTPException(502,str(exc))

@app.get('/health')
def health():return {'status':'ok','version':'0.2.0'}

@app.get('/api/settings')
def settings(x_admin_token:str|None=Header(default=None)):
    auth(x_admin_token)
    return {**strategy.SETTINGS.__dict__,'slots_ist':list(strategy.SETTINGS.slots_ist),'publication_mode':'manual_approval','youtube_upload':'not_enabled'}

@app.post('/api/trends/fetch')
async def fetch(x_admin_token:str|None=Header(default=None)):
    auth(x_admin_token)
    entries,errors=await strategy.collect()
    added=workflow.ingest(entries)
    return {'added':added,'candidates':len(entries),'errors':errors}

@app.get('/api/topics')
def topics(x_admin_token:str|None=Header(default=None),category:str|None=None,status:str|None=None):
    auth(x_admin_token)
    rows=core.list_topics()
    if category:rows=[x for x in rows if x.get('category')==category]
    if status:rows=[x for x in rows if x.get('status')==status]
    return rows

@app.get('/api/topics/{topic_id}')
def topic(topic_id:str,x_admin_token:str|None=Header(default=None)):
    auth(x_admin_token)
    try:return core.get_topic(topic_id)
    except Exception as exc:fail(exc)

@app.patch('/api/topics/{topic_id}')
def edit(topic_id:str,body:Edit,x_admin_token:str|None=Header(default=None)):
    auth(x_admin_token)
    try:return workflow.edit(topic_id,body.editor_notes,body.script)
    except Exception as exc:fail(exc)

@app.post('/api/topics/{topic_id}/draft')
async def draft(topic_id:str,x_admin_token:str|None=Header(default=None)):
    auth(x_admin_token)
    try:return await core.local_draft(core.get_topic(topic_id))
    except Exception as exc:fail(exc)

@app.post('/api/topics/{topic_id}/render')
async def render(topic_id:str,voice_enabled:bool=True,x_admin_token:str|None=Header(default=None)):
    auth(x_admin_token)
    try:return await video.render(core.get_topic(topic_id),with_voice=voice_enabled)
    except Exception as exc:fail(exc)

@app.post('/api/topics/{topic_id}/decision')
def decision(topic_id:str,body:Approval,x_admin_token:str|None=Header(default=None)):
    auth(x_admin_token)
    if body.action not in ('approve','reject','queue','revise'):
        raise HTTPException(400,'Action must be approve, reject, queue, or revise')
    targets={'approve':'approved','reject':'rejected','queue':'queued','revise':'drafted'}
    try:return workflow.transition(topic_id,targets[body.action],actor=body.actor[:80])
    except Exception as exc:fail(exc)

@app.get('/api/topics/{topic_id}/events')
def events(topic_id:str,x_admin_token:str|None=Header(default=None)):
    auth(x_admin_token)
    return workflow.list_events(topic_id)

@app.get('/api/topics/{topic_id}/video')
def media(topic_id:str,x_admin_token:str|None=Header(default=None)):
    auth(x_admin_token)
    try:t=core.get_topic(topic_id)
    except Exception as exc:fail(exc)
    path=Path(t.get('output_file',''))
    if not t.get('output_file') or not path.is_file() or path.resolve().parent!=(core.OUT/topic_id).resolve():
        raise HTTPException(404,'Preview not rendered')
    return FileResponse(path,media_type='video/mp4')

@app.get('/',response_class=HTMLResponse)
def home():
    return (Path(__file__).with_name('dashboard.html')).read_text(encoding='utf-8')
