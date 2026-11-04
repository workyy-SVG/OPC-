import json
import urllib.error

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from pathlib import Path

import core.ai as ai


def test_aliases_need_no_key_and_no_network(monkeypatch):
    monkeypatch.setattr(ai, 'complete', lambda *a, **k: pytest.fail('should not call network'))
    assert ai.match_headers(['订单号','货号','日期','销售件数']) == dict(order_id='订单号', sku='货号', order_at='日期', qty='销售件数')


def test_ai_header_results_are_validated(monkeypatch):
    monkeypatch.setattr(ai, 'complete', lambda *a: dict(order_id='invoice', sku='missing', order_at='when', qty='amount'))
    assert ai.match_headers(['invoice','when','amount'], use_ai=True) == dict(order_id='invoice', order_at='when', qty='amount')
    monkeypatch.setattr(ai, 'complete', lambda *a: dict(order_id='x', sku='x'))
    with pytest.raises(ai.AIError): ai.match_headers(['x'], use_ai=True)


def test_draft_box_math_unknown_product_and_missing_fields(monkeypatch):
    monkeypatch.setattr(ai, 'complete', lambda *a: {'items':[
        dict(sku='001', qty=240, box_count=10, pack_size=24, expected_at='2026-11-06'),
        dict(sku='invented', qty=None, expected_at=None)]})
    rows = ai.purchase_draft('采购', ['001'], '2026-11-04')
    assert rows[0]['qty'] == 240
    assert rows[1]['sku'] is None and rows[1]['qty'] is None


@pytest.mark.parametrize('item', [dict(qty=-1),dict(qty=float('inf')), dict(qty=1.5),
    dict(qty=9,box_count=2,pack_size=6),dict(expected_at='NaT')])
def test_invalid_model_values_rejected(monkeypatch,item):
    monkeypatch.setattr(ai, 'complete', lambda *a: {'items':[item]})
    with pytest.raises(ai.AIError): ai.purchase_draft('采购', ['001'], '2026-11-04')


def test_complete_request_and_safe_http_errors(monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY','test-secret')
    captured = []
    class Response:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self,*a): return json.dumps({'choices':[{'message':{'content':'{"answer":"依据不足"}'}}]}).encode()
    def send(request,timeout):
        captured.append(json.loads(request.data)); assert timeout == 45
        return Response()
    monkeypatch.setattr(ai.urllib.request,'urlopen',send)
    assert ai.explain('为什么',{'qty':None}) == '依据不足'
    assert captured[0]['model'] == 'qwen-plus'
    assert captured[0]['messages'][1]['content'].find('test-secret') == -1
    def fail(*args,**kwargs): raise urllib.error.HTTPError('url',401,'test-secret',{},None)
    monkeypatch.setattr(ai.urllib.request,'urlopen',fail)
    with pytest.raises(ai.AIError) as error: ai.explain('为什么',{})
    assert '401' in str(error.value) and 'test-secret' not in str(error.value)


def test_image_routes_to_vision_and_validates_size(monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY','test-key')
    bodies=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self,*a): return b'{"choices":[{"message":{"content":"{}"}}]}'
    def send(request,timeout): bodies.append(json.loads(request.data)); return Response()
    monkeypatch.setattr(ai.urllib.request,'urlopen',send)
    ai.complete('system',{}, image=(b'\x89PNG\r\n\x1a\nrest','image/png'))
    assert bodies[0]['model'] == 'qwen-vl-plus'
    assert bodies[0]['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    with pytest.raises(ai.AIError): ai.complete('s',{}, image=(b'bad','image/png'))
    with pytest.raises(ai.AIError): ai.complete('s',{}, 'deepseek',image=(b'bad','image/png'))


def test_no_key_manual_page_still_works(monkeypatch):
    monkeypatch.delenv('DASHSCOPE_API_KEY',raising=False)
    monkeypatch.setattr('ai_ui.configured', lambda provider: False)
    with pytest.raises(ai.AIError): ai.explain('为什么',{})
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=60).run()
    next(b for b in app.button if b.label=='用示例立即体验').click().run()
    app.radio(key='navigation').set_value('采购与到货').run()
    next(w for w in app.text_input if w.label=='采购描述或单据补充说明').set_value('订10件')
    next(w for w in app.checkbox if w.label.startswith('我同意发送')).check().run()
    assert not app.exception
    assert next(b for b in app.button if b.label=='生成采购草稿').disabled
    assert any('暂不可用，仍可手动填写' in item.value for item in app.caption)
    assert not any('DASHSCOPE_API_KEY' in item.value for item in app.caption)
    assert app.session_state.purchases == []


def test_ai_draft_page_never_writes_without_confirmation(monkeypatch):
    monkeypatch.setattr('ai_ui.configured', lambda provider: True)
    monkeypatch.setattr('ai_ui.purchase_draft', lambda text,skus,*a: [dict(sku=skus[0],qty=12,expected_at='2010-07-04',note='箱规明确')])
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=60).run()
    next(b for b in app.button if b.label=='用示例立即体验').click().run()
    app.radio(key='navigation').set_value('采购与到货').run()
    assert str(next(w for w in app.date_input if w.label=='相对日期参考日').value) == '2010-07-01'
    assert next(b for b in app.button if b.label=='生成采购草稿').disabled
    next(w for w in app.text_input if w.label=='采购描述或单据补充说明').set_value('订12件')
    next(w for w in app.checkbox if w.label.startswith('我同意发送')).check().run()
    next(b for b in app.button if b.label=='生成采购草稿').click().run()
    assert not app.exception and not app.session_state.purchases
    next(w for w in app.checkbox if w.label.startswith('我已逐行核对')).check().run()
    next(b for b in app.button if b.label=='确认草稿并登记采购').click().run()
    assert not app.exception
    assert app.session_state.purchases[0]['qty']==12
    assert app.session_state.purchases[0]['receipts']==[]


def test_community_cloud_secrets_are_read(monkeypatch):
    import streamlit as st
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    monkeypatch.setattr(st, 'secrets', {'DASHSCOPE_API_KEY':'test-cloud-secret'})
    assert ai.configured('qwen')
    assert ai.setting('DASHSCOPE_API_KEY') == 'test-cloud-secret'
    assert ai.setting('QWEN_MODEL','qwen-plus') == 'qwen-plus'


@pytest.mark.parametrize('secrets', [
    {'qwen':{'api_key':' test-grouped '}},
    {'ai':{'DASHSCOPE_API_KEY':' test-grouped '}},
    {'QWEN_API_KEY':' test-grouped '},
])
def test_grouped_qwen_secrets(monkeypatch,secrets):
    import streamlit as st
    monkeypatch.delenv('DASHSCOPE_API_KEY',raising=False)
    monkeypatch.setattr(st,'secrets',secrets)
    assert ai.setting('DASHSCOPE_API_KEY')=='test-grouped'
    assert ai.configured('qwen')


def test_grouped_deepseek_secrets(monkeypatch):
    import streamlit as st
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.setattr(st,'secrets',{'deepseek':{'api_key':'test-grouped'}})
    assert ai.setting('DEEPSEEK_API_KEY')=='test-grouped'
