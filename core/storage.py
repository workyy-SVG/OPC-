"""Optional service-side Supabase persistence; never exposes credentials to UI."""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


class StorageError(OSError):
    pass


def secret(name, default=''):
    value=os.environ.get(name)
    if not value:
        import streamlit as st
        try: value=st.secrets.get(name)
        except FileNotFoundError: value=None
    return value.strip() if isinstance(value,str) and value.strip() else default


def remote_enabled():
    mode=secret('STORAGE_BACKEND','auto').lower()
    if mode=='local': return False
    if mode not in ('auto','supabase'): raise StorageError('STORAGE_BACKEND只能为local或supabase')
    url,key=secret('SUPABASE_URL'),secret('SUPABASE_SERVICE_ROLE_KEY')
    if mode=='auto' and not url and not key: return False
    if not url or not key: raise StorageError('Supabase配置不完整，请在服务端填写URL和服务端密钥')
    try:
        parsed=urllib.parse.urlsplit(url)
        port=parsed.port
    except ValueError:
        raise StorageError('Supabase项目地址格式不正确') from None
    if parsed.scheme!='https' or not parsed.hostname or not re.fullmatch(r'[a-z0-9-]+\.supabase\.co',parsed.hostname) or parsed.username or parsed.password or port or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise StorageError('请填写标准https://项目编号.supabase.co地址')
    if key.startswith('sb_publishable_'): raise StorageError('需要服务端service_role或secret密钥，不能使用publishable密钥')
    return True


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def rpc(name, parameters):
    if name not in ('opc_workspace_save','opc_workspace_read','opc_quota_status','opc_quota_reserve','opc_quota_floor'):
        raise StorageError('不支持的数据库操作')
    if not remote_enabled(): raise StorageError('尚未启用Supabase')
    key=secret('SUPABASE_SERVICE_ROLE_KEY')
    headers={'apikey':key,'Content-Type':'application/json'}
    if not key.startswith('sb_secret_'): headers['Authorization']='Bearer '+key
    body=json.dumps(parameters,ensure_ascii=False,allow_nan=False).encode()
    if len(body)>16*1024*1024: raise StorageError('数据库快照过大，请拆分工作区')
    request=urllib.request.Request(secret('SUPABASE_URL').rstrip('/')+'/rest/v1/rpc/'+name,
        data=body,headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=20) as response:
            raw=response.read(16*1024*1024+1)
        if len(raw)>16*1024*1024: raise StorageError('数据库快照过大，请拆分工作区')
        return json.loads(raw)
    except urllib.error.HTTPError as error:
        try:
            detail=json.loads(error.read(2048))
            message=detail.get('message','') if isinstance(detail,dict) else ''
        except (ValueError,TypeError): message=''
        if message=='OPC_REVISION_CONFLICT': raise StorageError('工作区已在其他会话更新，请恢复最新记录后重试') from None
        if message=='OPC_NOT_FOUND': raise StorageError('找不到工作区，请核对恢复码') from None
        raise StorageError(f'数据库请求失败（HTTP {error.code}），请检查SQL初始化和服务端密钥，未切换到本机存储') from None
    except (urllib.error.URLError,TimeoutError,ValueError,OSError):
        raise StorageError('数据库连接或响应异常，操作未确认；请恢复最新记录后重试') from None


def migration_floor():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from .numbers import integer
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    if secret('AI_USAGE_MIGRATION_DAY')==today:
        try: used=integer(secret('AI_USAGE_MIGRATION_USED','0'),0,100)
        except ValueError: raise StorageError('迁移日已用额度须为0至100的整数') from None
        if used:
            result=rpc('opc_quota_floor',dict(p_day=today,p_used=used))
            if not isinstance(result,int) or isinstance(result,bool) or not used<=result<=100:
                raise StorageError('额度迁移未确认，未允许AI调用')


def usage():
    migration_floor()
    result=rpc('opc_quota_status',{})
    used=result.get('used') if isinstance(result,dict) else None
    if not isinstance(used,int) or isinstance(used,bool) or not 0<=used<=100:
        raise StorageError('数据库额度响应不合法')
    return used,100-used


def reserve():
    migration_floor()
    result=rpc('opc_quota_reserve',{})
    if result is False:
        raise ValueError('今日千问和DeepSeek合计100次额度已用完，北京时间零点重置')
    if result is not True:
        raise StorageError('数据库额度扣减未确认，未发送AI请求')

APP_REVISION = "audit-fixes-20261104"
