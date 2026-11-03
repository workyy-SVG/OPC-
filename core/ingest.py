import pandas as pd
REQUIRED = ['order_id','sku','order_at','qty']
def parse_orders(raw, mapping=None, date_format='%Y-%m-%d'):
    df = raw.rename(columns=mapping or {}).copy().fillna('')
    if not set(REQUIRED) <= set(df): raise ValueError('缺少必填列：'+','.join(set(REQUIRED)-set(df)))
    clean, issues, seen = [], [], {}
    summary = dict(valid=0,cancelled=0,returned=0,suspicious=0,error=0,excluded=0,duplicate=0)
    conflicts=set()
    if 'line_id' in df:
        for key,g in df[df.line_id.astype(str)!=''].groupby(['order_id','line_id']):
            if len(g.drop_duplicates())>1: conflicts.add(key)
    for i,r in df.iterrows():
        d=r.to_dict(); reason=None; category='error'
        try:
            if any(str(d[k]).strip()=='' for k in REQUIRED): raise ValueError()
            d['order_at']=pd.to_datetime(d['order_at'],format=date_format,errors='raise').normalize()
            q=float(d['qty'])
            if not q.is_integer(): raise ValueError()
            d['qty']=int(q)
        except (ValueError,TypeError,OverflowError): reason='INVALID_FIELD'
        key=(d.get('order_id'),d.get('line_id'))
        if not reason and key in conflicts: reason='CONFLICTING_LINE_ID'
        if not reason and d.get('line_id'):
            if key in seen: reason='DUPLICATE_LINE'; category='duplicate'
            seen[key]=True
        if not reason and (str(d['order_id']).upper().startswith('C') or str(d.get('status','')).lower() in ['cancelled','canceled','取消']): reason='CANCELLED';category='cancelled'
        if not reason and d['qty']<0: reason='RETURN';category='returned'
        if not reason and (d['qty']==0 or str(d.get('status','completed')).lower() not in ['','completed','complete','已完成']): reason='NOT_COMPLETED'
        if not reason and d['sku'] in ['POST','D','M','C2','DOT','BANK CHARGES']: reason='NON_PRODUCT';category='excluded'
        if reason:
            summary[category]+=1;issues.append(dict(row_no=i+2,reason_code=reason,hint=('修正原始记录后重新导入' if reason in ['INVALID_FIELD','CONFLICTING_LINE_ID'] else '已按规则处理，无需为此修改原始记录'),**r.to_dict()));continue
        clean.append(d);summary['valid']+=1
    out=pd.DataFrame(clean,columns=df.columns)
    if len(out) and 'line_id' not in df:
        mask=out.duplicated(REQUIRED,keep=False);summary['suspicious']=int(mask.sum())
        for _,r in out[mask].iterrows(): issues.append(dict(row_no='',reason_code='SUSPECT_DUPLICATE',hint='无稳定行号，保留销量，请人工确认',**r.to_dict()))
    return out,pd.DataFrame(issues),summary
