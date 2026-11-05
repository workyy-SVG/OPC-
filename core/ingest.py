import pandas as pd
from .numbers import integer, MAX_QUANTITY

REQUIRED = ['order_id','sku','order_at','qty']
OPTIONAL_ALIASES = {'订单状态':'status','处理状态':'status','订单行编号':'line_id','行编号':'line_id'}


def parse_orders(raw, mapping=None, date_format='%Y-%m-%d', source='generic'):
    df = raw.rename(columns=mapping or {}).copy().fillna('')
    for alias, canonical in OPTIONAL_ALIASES.items():
        if alias in df and canonical in df:
            if not df[alias].astype(str).str.strip().equals(df[canonical].astype(str).str.strip()):
                raise ValueError(f'{alias}与{canonical}内容冲突，请明确选择保留一列')
            df = df.drop(columns=alias)
        elif alias in df:
            df = df.rename(columns={alias:canonical})
    if df.columns.duplicated().any():
        raise ValueError('字段名称重复，请调整映射')
    if not set(REQUIRED) <= set(df):
        raise ValueError('缺少必填列：'+','.join(sorted(set(REQUIRED)-set(df))))
    for key in ('order_id','sku','line_id','status'):
        if key in df:
            df[key] = df[key].astype(str).str.strip()
    clean, issues, seen = [], [], set()
    summary = dict(valid=0,cancelled=0,returned=0,suspicious=0,error=0,excluded=0,duplicate=0)
    conflicts = set()
    if 'line_id' in df:
        for key, group in df[df.line_id.ne('')].groupby(['order_id','line_id']):
            if len(group[REQUIRED + (['status'] if 'status' in df else [])].drop_duplicates()) > 1:
                conflicts.add(key)

    def issue(row, number, reason, hint):
        # Preserve original diagnostic fields without colliding with this run.
        original = dict(row)
        for field in ('row_no','reason_code','hint'):
            if field in original:
                target='original_'+field
                while target in original: target='original_'+target
                original[target]=original.pop(field)
        issues.append(dict(original, row_no=number, reason_code=reason, hint=hint))

    for number, (_, row) in enumerate(df.iterrows(), 2):
        d = row.to_dict(); reason = None; category = 'error'
        try:
            if any(str(d[k]).strip()=='' for k in REQUIRED):
                raise ValueError()
            parsed = pd.to_datetime(d['order_at'],format=date_format,errors='raise')
            if pd.isna(parsed) or parsed.tzinfo is not None:
                raise ValueError()
            d['order_at'] = parsed.normalize()
            d['qty'] = integer(d['qty'], -MAX_QUANTITY)
        except (ValueError,TypeError,OverflowError):
            reason = 'INVALID_FIELD'
        key = (d.get('order_id'),d.get('line_id'))
        if not reason and key in conflicts:
            reason = 'CONFLICTING_LINE_ID'
        if not reason and d.get('line_id'):
            if key in seen:
                reason = 'DUPLICATE_LINE'; category = 'duplicate'
            seen.add(key)
        status = str(d.get('status','completed')).strip().lower()
        if not reason and ((source=='uci' and str(d['order_id']).upper().startswith('C')) or status in ('cancelled','canceled','取消','已取消')):
            reason='CANCELLED'; category='cancelled'
        if not reason and d['qty']<0:
            reason='RETURN'; category='returned'
        if not reason and (d['qty']==0 or status not in ('','completed','complete','已完成','完成')):
            reason='NOT_COMPLETED'
        if not reason and source=='uci' and d['sku'] in ('POST','D','M','C2','DOT','BANK CHARGES'):
            reason='NON_PRODUCT'; category='excluded'
        if reason:
            summary[category]+=1
            issue(row.to_dict(),number,reason,'修正原始记录后重新导入' if category=='error' else '已按规则处理，无需为此修改原始记录')
        else:
            clean.append(d); summary['valid']+=1
    out = pd.DataFrame(clean,columns=df.columns)
    if len(out):
        missing_ids = out.line_id.eq('') if 'line_id' in out else pd.Series(True,index=out.index)
        mask = out.duplicated(REQUIRED,keep=False) & missing_ids
        summary['suspicious'] = int(mask.sum())
        for _,row in out[mask].iterrows():
            issue(row.to_dict(),'', 'SUSPECT_DUPLICATE','无稳定行号，保留销量，请人工确认')
    return out,pd.DataFrame(issues),summary

APP_REVISION = "audit-fixes-20261104"
