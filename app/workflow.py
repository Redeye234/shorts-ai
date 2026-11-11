"""SQLite migration and editor-only approval workflow for video publishing readiness."""
import json
import sqlite3
from datetime import datetime, timezone
from . import core

TRANSITIONS={
 'discovered': {'needs_review','rejected'},
 'needs_review':{'drafted','rejected'},
 'drafted':{'rendered','needs_review','rejected'},
 'rendered':{'approved','drafted','rejected'},
 'approved':{'rendered','rejected','queued'},
 'queued':{'approved','uploaded_private'},
 'uploaded_private':set(),
 'rejected':{'needs_review'},
}

def migrate():
    with core.connection() as con:
        columns={row['name'] for row in con.execute('PRAGMA table_info(topics)')}
        fields={
            'adapter':"TEXT NOT NULL DEFAULT 'google_trends'",
            'category':"TEXT NOT NULL DEFAULT 'global'",
            'features_json':"TEXT NOT NULL DEFAULT '{}'",
            'regions_json':"TEXT NOT NULL DEFAULT '[]'",
            'language':"TEXT NOT NULL DEFAULT 'hinglish'",
            'voice_file':"TEXT NOT NULL DEFAULT ''",
            'approved_at':"TEXT NOT NULL DEFAULT ''",
            'approved_by':"TEXT NOT NULL DEFAULT ''",
            'youtube_video_id':"TEXT NOT NULL DEFAULT ''",
        }
        for key,definition in fields.items():
            if key not in columns: con.execute(f'ALTER TABLE topics ADD COLUMN {key} {definition}')
        con.execute('''CREATE TABLE IF NOT EXISTS editorial_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id TEXT NOT NULL,
              event TEXT NOT NULL, timestamp TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')''')
        con.commit()


def record(con,topic_id,event,detail=''):
    con.execute('INSERT INTO editorial_events(topic_id,event,timestamp,detail) VALUES(?,?,?,?)',
                (topic_id,event,datetime.now(timezone.utc).isoformat(),detail))


def ingest(items):
    """Merge repeated subjects across regions; preserve editorial state."""
    count=0
    with core.connection() as con:
        for entry in items:
            previous=con.execute('SELECT * FROM topics WHERE id=?',(entry['id'],)).fetchone()
            if previous:
                current=set(json.loads(previous['regions_json']))
                current.add(entry['region'])
                links=list(dict.fromkeys(json.loads(previous['sources_json'])+entry['sources']))[:10]
                score=max(previous['score'],entry['score'])
                con.execute('''UPDATE topics SET score=?, sources_json=?, regions_json=? WHERE id=?''',
                            (score,json.dumps(links),json.dumps(sorted(current)),entry['id']))
                continue
            status='needs_review' # Every story needs verified editorial facts.
            con.execute('''INSERT INTO topics(id,title,region,traffic,discovered_at,sources_json,score,status,
              adapter,category,features_json,regions_json,language) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (entry['id'],entry['title'],entry['region'],entry['traffic'],datetime.now(timezone.utc).isoformat(),
               json.dumps(entry['sources']),entry['score'],status,entry['adapter'],entry['category'],
               json.dumps(entry['features']),json.dumps([entry['region']]),'hinglish'))
            record(con,entry['id'],'discovered',entry['adapter'])
            count+=1
        con.commit()
    return count


def edit(topic_id,notes=None,script=None):
    with core.connection() as con:
        obj=con.execute('SELECT * FROM topics WHERE id=?',(topic_id,)).fetchone()
        if not obj:raise KeyError(topic_id)
        if obj['status'] in ('approved','queued','uploaded_private'):
            raise ValueError('Approved/queued/uploaded content is locked; reject before revising.')
        fields={}
        if notes is not None:fields['editor_notes']=notes
        if script is not None:
            if len(script.strip())<60:raise ValueError('Script needs at least 60 characters')
            if len((notes if notes is not None else obj['editor_notes']).strip())<50:
                raise ValueError('Save at least 50 characters of verified editorial notes first')
            fields['script']=script
            fields['status']='drafted'
            fields['output_file']=''
            fields['voice_file']=''
        if not fields:return core.get_topic(topic_id)
        con.execute('UPDATE topics SET '+', '.join(f'{k}=?' for k in fields)+' WHERE id=?',(*fields.values(),topic_id))
        record(con,topic_id,'edited',','.join(fields))
        con.commit()
    return core.get_topic(topic_id)


def transition(topic_id,target,actor='editor'):
    with core.connection() as con:
        obj=con.execute('SELECT * FROM topics WHERE id=?',(topic_id,)).fetchone()
        if not obj:raise KeyError(topic_id)
        current=obj['status']
        if target not in TRANSITIONS.get(current,set()):raise ValueError(f'Invalid transition {current} -> {target}')
        if target in ('approved','queued'):
            if not obj['output_file'] or not obj['voice_file']:
                raise ValueError('Video with Hinglish audio must be rendered before approval/queueing')
            from pathlib import Path
            if not Path(obj['output_file']).is_file() or not Path(obj['voice_file']).is_file():
                raise ValueError('Video or voice file missing')
        if target=='approved':
            con.execute('UPDATE topics SET status=?,approved_by=?,approved_at=? WHERE id=?',
                        (target,actor,datetime.now(timezone.utc).isoformat(),topic_id))
        else:con.execute('UPDATE topics SET status=? WHERE id=?',(target,topic_id))
        record(con,topic_id,target,actor)
        con.commit()
    return core.get_topic(topic_id)


def list_events(topic_id):
    with core.connection() as con:
        return [dict(r) for r in con.execute('SELECT * FROM editorial_events WHERE topic_id=? ORDER BY id DESC',(topic_id,))]
