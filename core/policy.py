import math, hashlib, json
import pandas as pd
from .numbers import integer
VERSION='1.2.0'
def order_quantity(mean, buffer, stock, lead, minimum, pack):
    target=math.ceil(mean*(lead+1)+buffer); gap=max(0,target-stock)
    return (math.ceil(max(gap,minimum)/pack)*pack if gap else 0),target

def recommend(history, stock, config, as_of):
    stock=dict(stock)
    config=dict(config)
    date=pd.Timestamp(as_of).normalize(); reasons=[]
    result=dict(sku=stock.get('sku',''),as_of=str(date.date()),status='人工复核',qty=None,rule_version=VERSION,source=stock.get('source','用户输入'))
    from .validation import parameter_status
    result['parameter_kind']=stock.get('parameter_kind','未核实')
    result['evidence_type']='历史销量规则建议；未经商户经营效果验证'
    if config.get('require_actual') and not parameter_status(stock): reasons.append('UNVERIFIED_PARAMETERS')
    for k in ['on_hand','on_order','lead_days','pack_size','min_order']:
        try:
            stock[k]=integer(stock[k],1 if k in ['lead_days','pack_size'] else 0)
        except (KeyError,ValueError,TypeError,OverflowError): reasons.append('INVALID_'+k.upper())
    if str(stock.get('as_of',''))[:10]!=str(date.date()): reasons.append('SNAPSHOT_DATE_MISMATCH')
    confirmed=stock.get('arrival_confirmed',False)
    if not (pd.api.types.is_bool(confirmed) and bool(confirmed)): reasons.append('ARRIVAL_UNCONFIRMED')
    if history.date.duplicated().any(): raise ValueError('历史日期重复，请先按商品和日期汇总')
    s=pd.to_numeric(history.set_index('date').sold_qty,errors='coerce').sort_index(); s=s[s.index<date]
    if (s.dropna()<0).any() or not s.dropna().map(math.isfinite).all(): raise ValueError('历史销量须为有限非负数')
    if 'coverage_status' in history:
        known=history.set_index('date').coverage_status.eq('confirmed')
        s=s.where(known.reindex(s.index))
    window=integer(config.get('window',28),1,366); quantile=float(config.get('quantile',.9))
    if window<1 or not 0<quantile<=1: raise ValueError('窗口和分位数不合法')
    recent=s.reindex(pd.date_range(date-pd.Timedelta(days=window),periods=window))
    if recent.isna().any(): reasons.append('HISTORY_GAP')
    if config.get('policy','adaptive') not in ('adaptive','baseline'): raise ValueError('无效补货规则')
    for key, default in [('adaptive_scale',1),('baseline_ratio',.2)]:
        value=float(config.get(key,default))
        if not math.isfinite(value) or value<0: raise ValueError('缓冲比例须为有限非负数')
        config[key]=value
    errors=[]
    if not reasons:
        lead=int(stock['lead_days']); h=lead+1; mean=float(recent.mean())
        continuous=s.reindex(pd.date_range(s.index.min(),date-pd.Timedelta(days=1)))
        prediction=continuous.rolling(window).mean().shift(1)*h
        actual=continuous.rolling(h).sum().shift(-(h-1))
        errors=(actual-prediction).dropna().tolist()
        errors=errors[-60:]
        if len(errors)<20: reasons.append('INSUFFICIENT_ERROR_WINDOWS')
        else:
            buffer=max(0,sorted(errors)[math.ceil(quantile*len(errors))-1]) *float(config.get('adaptive_scale',1)) if config.get('policy','adaptive')=='adaptive' else mean*h*config.get('baseline_ratio',.2)
            ip=int(stock['on_hand'])+int(stock['on_order']); qty,target=order_quantity(mean,buffer,ip,lead,int(stock['min_order']),int(stock['pack_size']))
            result.update(status='建议下单' if qty else '暂不补货',qty=qty,inventory_position=ip,forecast_lead=mean*lead,buffer=buffer,target=target,mean=mean,protection_days=h,window_start=str(recent.index.min().date()),window_end=str(recent.index.max().date()),sample_count=window,error_samples=len(errors))
    result['reason_codes']=reasons
    result['run_id']=hashlib.sha256((history.to_csv(index=False)+json.dumps([stock,config,str(date)],sort_keys=True,default=str)).encode()).hexdigest()[:16]
    return result

APP_REVISION = "audit-fixes-20261104"
