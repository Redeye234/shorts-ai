"""Optional explicit CLI to upload an approved Short as PRIVATE to a verified channel.

Usage:
  python scripts/upload_private.py TOPIC_ID --channel-id UCxxxxxxxxxx \
      --client-secrets path/to/client_secret.json

This command never makes a video public. It validates the authenticated channel ID.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app import core, workflow

SCOPES=['https://www.googleapis.com/auth/youtube.upload',
        'https://www.googleapis.com/auth/youtube.readonly']


def build_service(secrets:Path,token_file:Path):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError('pip install -r requirements-youtube.txt') from exc
    creds=None
    if token_file.exists():creds=Credentials.from_authorized_user_file(str(token_file),SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secrets.exists():raise FileNotFoundError(f'OAuth client secrets missing: {secrets}')
            flow=InstalledAppFlow.from_client_secrets_file(str(secrets),SCOPES)
            creds=flow.run_local_server(port=0,open_browser=True)
        token_file.parent.mkdir(parents=True,exist_ok=True)
        token_file.write_text(creds.to_json(),encoding='utf-8')
        if os.name!='nt':os.chmod(token_file,0o600)
    return build('youtube','v3',credentials=creds)


def upload(topic_id:str,expected_channel_id:str,secrets:Path,token_file:Path):
    from googleapiclient.http import MediaFileUpload
    item=core.get_topic(topic_id)
    if item['status'] not in ('approved','queued'):
        raise ValueError('Video must be explicitly approved in the dashboard first')
    if item['youtube_video_id']:
        raise ValueError(f'Already uploaded: {item["youtube_video_id"]}')
    file=Path(item['output_file'])
    if not file.is_file() or not item['voice_file'] or not Path(item['voice_file']).is_file():
        raise ValueError('Rendered voice video missing')
    service=build_service(secrets,token_file)
    result=service.channels().list(part='id,snippet',mine=True).execute()
    channels=result.get('items',[])
    if len(channels)!=1 or channels[0]['id']!=expected_channel_id:
        raise ValueError('Authenticated YouTube channel does not match --channel-id. No upload attempted.')
    title=item['title'][:100]
    description=(f'Original Hinglish explainer.\n\nSources for fact checking:\n'+
                 '\n'.join(item['sources'][:6])+'\n\n#Shorts')[:4500]
    body={'snippet':{'title':title,'description':description,'categoryId':'28' if item.get('category')=='tech' else '22'},
          'status':{'privacyStatus':'private','selfDeclaredMadeForKids':False}}
    media=MediaFileUpload(str(file),mimetype='video/mp4',chunksize=8*1024*1024,resumable=True)
    request=service.videos().insert(part='snippet,status',body=body,media_body=media)
    response=None
    while response is None:
        _,response=request.next_chunk()
    video_id=response.get('id')
    if not video_id:raise RuntimeError('YouTube did not return video ID; check Studio before retrying')
    # Strict idempotency: record API success immediately.
    with core.connection() as con:
        con.execute('UPDATE topics SET youtube_video_id=?,status=? WHERE id=? AND youtube_video_id=""',
                    (video_id,'uploaded_private',topic_id))
        workflow.record(con,topic_id,'uploaded_private',video_id)
        con.commit()
    return {'youtube_video_id':video_id,'privacy':'private','channel_id':expected_channel_id}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Explicit private upload of editor-approved narrated video')
    parser.add_argument('topic_id')
    parser.add_argument('--channel-id',required=True)
    parser.add_argument('--client-secrets',type=Path,default=Path('client_secret.json'))
    parser.add_argument('--token-file',type=Path,default=Path('credentials/youtube-token.json'))
    args=parser.parse_args()
    workflow.migrate()
    print(json.dumps(upload(args.topic_id,args.channel_id,args.client_secrets,args.token_file),indent=2))
