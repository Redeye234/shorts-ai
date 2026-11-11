import os
import tempfile
from pathlib import Path

os.environ['SHORTSPILOT_HOME']=tempfile.mkdtemp(prefix='sp-test-')
from app import core

XML=b'''<?xml version="1.0"?><rss xmlns:ht="https://trends.google.com/trending/rss"><channel><item><title>New AI phone</title><ht:approx_traffic>20K+</ht:approx_traffic><ht:news_item><ht:news_item_url>https://example.com/source</ht:news_item_url></ht:news_item></item></channel></rss>'''

def test_feed():
    rows=core.parse_rss(XML,'IN')
    assert len(rows)==1
    assert rows[0]['score']>50
    assert rows[0]['sources']==['https://example.com/source']


def test_status_and_notes():
    with core.connection() as c:
        c.execute("INSERT INTO topics (id,title,region,traffic,discovered_at,sources_json,score) VALUES ('abc','New AI phone','IN','20K+','now','[]',80)")
        c.commit()
    result=core.update_topic('abc',editor_notes='Verified facts to consider')
    assert result['editor_notes']=='Verified facts to consider'
    try: core.update_topic('abc',status='published')
    except ValueError: pass
    else: assert False


def test_render_card():
    output=Path(os.environ['SHORTSPILOT_HOME'])/'card.png'
    core.render_card('Test Title','An original explanation',output,1,1)
    assert output.is_file()
