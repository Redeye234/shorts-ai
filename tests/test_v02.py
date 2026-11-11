from app import strategy,workflow,core

RSS=b'''<?xml version="1.0"?><rss xmlns:ht="https://trends.google.com/trending/rss"><channel><item><title>New AI phone India</title><ht:approx_traffic>20K+</ht:approx_traffic><ht:news_item><ht:news_item_url>https://example.org/facts</ht:news_item_url></ht:news_item></item></channel></rss>'''

def test_strategy_and_region_merging():
    parsed=strategy.parse_trends_xml(RSS,'IN')
    assert len(parsed)==1 and parsed[0]['category']=='tech'
    workflow.migrate()
    before=workflow.ingest(parsed)
    repeat=strategy.parse_trends_xml(RSS,'US')
    assert before in (0,1)
    assert workflow.ingest(repeat)==0
    item=core.get_topic(parsed[0]['id'])
    assert item['status']=='needs_review'
    assert {'IN','US'} <= set(item['regions'])

def test_approval_gated():
    parsed=strategy.parse_trends_xml(RSS,'IN')[0]
    workflow.ingest([parsed])
    try:workflow.transition(parsed['id'],'approved')
    except ValueError:pass
    else:assert False

def test_volume_and_scope():
    assert strategy.estimate_volume('20K+')==20000
    assert strategy.estimate_volume('1M+')==1000000
    assert strategy.SETTINGS.language=='hinglish'
    assert strategy.SETTINGS.shorts_per_day==3
    assert strategy.SETTINGS.scope=='india-global'
