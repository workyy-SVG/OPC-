from concurrent.futures import ThreadPoolExecutor

import pytest
import core.ai_quota as quota
import core.ai as ai


def test_shared_limit_and_new_day(monkeypatch):
    monkeypatch.setattr(quota,'today',lambda:'2026-11-04')
    for _ in range(100): quota.reserve()
    assert quota.usage() == (100,0)
    with pytest.raises(ValueError): quota.reserve()
    monkeypatch.setattr(quota,'today',lambda:'2026-11-05')
    assert quota.usage() == (0,100)
    quota.reserve()
    assert quota.usage() == (1,99)


def test_concurrent_requests_never_exceed_limit():
    quota.usage()
    def attempt(_):
        try: quota.reserve(); return 1
        except ValueError: return 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt,range(120))) == 100
    assert quota.usage() == (100,0)


def test_both_providers_share_budget_and_block_before_network(monkeypatch):
    monkeypatch.setattr(ai,'setting',lambda *a:'test-key')
    calls=[]
    def fail(*a,**kw): calls.append(1); raise TimeoutError()
    monkeypatch.setattr(ai.urllib.request,'urlopen',fail)
    for provider in ['qwen','deepseek']:
        with pytest.raises(ai.AIError): ai.complete('system',{},provider)
    assert quota.usage() == (2,98)
    for _ in range(98): quota.reserve()
    with pytest.raises(ai.AIError,match='100次'): ai.complete('system',{},'qwen')
    assert len(calls)==2


def test_sidebar_displays_updated_shared_remaining():
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=60).run()
    assert not app.exception
    assert next(m for m in app.sidebar.metric if m.label=='今日剩余调用').value=='100 / 100'
    quota.reserve()
    app.run()
    assert not app.exception
    assert next(m for m in app.sidebar.metric if m.label=='今日剩余调用').value=='99 / 100'


@pytest.mark.parametrize('unavailable',[False,True])
def test_cloud_status_reuses_success_and_failure_until_refresh(monkeypatch,unavailable):
    import time
    from core import storage
    from streamlit.testing.v1 import AppTest
    calls=[]
    monkeypatch.setattr(storage,'remote_enabled',lambda:True)
    def usage():
        calls.append(1)
        if unavailable: raise storage.StorageError('连接超时')
        return (len(calls),100-len(calls))
    monkeypatch.setattr(quota,'usage',usage)
    app=AppTest.from_string('''
import streamlit as st
from core.ai_quota import display_usage
from core.storage import StorageError
try:
    st.write(display_usage())
except StorageError as error:
    st.warning(str(error))
''',default_timeout=5).run()
    app.run()
    assert not app.exception and len(calls)==1
    cached=dict(app.session_state['_quota_display'])
    app.session_state['_quota_display']=cached|{'at':time.monotonic()-31}
    app.run()
    assert not app.exception and len(calls)==2
    app.session_state['_quota_display']=dict(app.session_state['_quota_display'])|{'day':'2000-01-01'}
    app.run()
    assert not app.exception and len(calls)==3
