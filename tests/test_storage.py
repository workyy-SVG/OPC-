import io
import json
import urllib.error
import pytest
from core import storage
from core.purchasing import save_workspace, read_workspace, load_workspace


def enable(monkeypatch):
    monkeypatch.setenv('STORAGE_BACKEND','supabase')
    monkeypatch.setenv('SUPABASE_URL','https://example-project.supabase.co')
    monkeypatch.setenv('SUPABASE_SERVICE_ROLE_KEY','test-server-secret')


def test_partial_remote_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv('STORAGE_BACKEND','supabase')
    monkeypatch.delenv('SUPABASE_URL',raising=False)
    monkeypatch.delenv('SUPABASE_SERVICE_ROLE_KEY',raising=False)
    with pytest.raises(storage.StorageError):storage.remote_enabled()


@pytest.mark.parametrize('url',['http://example.supabase.co','https://example.supabase.co.evil.test',
    'https://user:password@example.supabase.co','https://example.supabase.co/path'])
def test_remote_url_validation(monkeypatch,url):
    enable(monkeypatch);monkeypatch.setenv('SUPABASE_URL',url)
    with pytest.raises(storage.StorageError):storage.remote_enabled()


def test_quota_remote_cap_is_enforced_before_ai_network(monkeypatch):
    from core import ai, ai_quota
    enable(monkeypatch)
    monkeypatch.setenv('DASHSCOPE_API_KEY','test-ai-key')
    monkeypatch.setattr(storage,'rpc',lambda name,args:False if name=='opc_quota_reserve' else {'used':100})
    assert ai_quota.usage()==(100,0)
    monkeypatch.setattr(ai.urllib.request,'urlopen',lambda *a,**k:pytest.fail('AI must not be called'))
    with pytest.raises(ai.AIError,match='100次'):ai.complete('system',{})


def test_remote_storage_failure_does_not_fallback(monkeypatch,tmp_path):
    enable(monkeypatch)
    import pandas as pd
    def fail(*a,**k):raise storage.StorageError('remote unavailable')
    monkeypatch.setattr(storage,'rpc',fail)
    path=tmp_path/('a'*32+'.json')
    with pytest.raises(storage.StorageError):save_workspace(path,[],pd.DataFrame(),[])
    assert not path.exists()


def test_remote_request_uses_fixed_rpc_and_redacts_errors(monkeypatch):
    enable(monkeypatch)
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,*a):return b'{"used":7}'
    class Opener:
        def open(self,request,timeout):
            calls.append(request)
            assert timeout==20
            return Response()
    monkeypatch.setattr(storage.urllib.request,'build_opener',lambda *a:Opener())
    assert storage.usage()==(7,93)
    assert calls[0].full_url=='https://example-project.supabase.co/rest/v1/rpc/opc_quota_status'
    assert calls[0].get_header('Apikey')=='test-server-secret'
    class Failed:
        def open(self,*a,**k):
            raise urllib.error.HTTPError('url',401,'test-server-secret',{},io.BytesIO(b'{"message":"test-server-secret"}'))
    monkeypatch.setattr(storage.urllib.request,'build_opener',lambda *a:Failed())
    with pytest.raises(storage.StorageError) as error:storage.usage()
    assert '401' in str(error.value) and 'test-server-secret' not in str(error.value)


def test_remote_workspace_save_and_recovery(monkeypatch,tmp_path):
    import pandas as pd
    enable(monkeypatch)
    snapshots={}
    def rpc(name,args):
        if name=='opc_workspace_save':
            current=snapshots.get(args['p_id'],{}).get('revision',0)
            if current!=args['p_expected_revision']:raise storage.StorageError('revision conflict')
            snapshots[args['p_id']]=args['p_payload']|{'revision':current+1}
            return current+1
        return snapshots[args['p_id']]
    monkeypatch.setattr(storage,'rpc',rpc)
    inventory=pd.DataFrame([dict(sku='001',as_of='2025-04-01',on_hand=10,on_order=0,lead_days=3,
        pack_size=1,min_order=0,arrival_confirmed=True,source='盘点',parameter_kind='实际盘点',verified=True)])
    code='a'*32
    assert save_workspace(tmp_path/(code+'.json'),[],inventory,[],source_hash='data',expected_revision=0)==1
    payload=json.loads(read_workspace(code))
    assert payload['source_hash']=='data' and payload['revision']==1
    assert load_workspace(json.dumps(payload))[1].iloc[0].on_hand==10
    with pytest.raises(storage.StorageError):save_workspace(tmp_path/(code+'.json'),[],inventory,[],expected_revision=0)


def test_migration_floor_only_applies_on_configured_beijing_day(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    enable(monkeypatch)
    calls=[]
    def rpc(name,args):
        calls.append((name,args))
        return 80 if name=='opc_quota_floor' else {'used':80}
    monkeypatch.setattr(storage,'rpc',rpc)
    monkeypatch.setenv('AI_USAGE_MIGRATION_DAY',datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat())
    monkeypatch.setenv('AI_USAGE_MIGRATION_USED','80')
    assert storage.usage()==(80,20)
    assert calls[0][0]=='opc_quota_floor'
    calls.clear()
    monkeypatch.setenv('AI_USAGE_MIGRATION_DAY','2000-01-01')
    storage.usage()
    assert all(name!='opc_quota_floor' for name,args in calls)
