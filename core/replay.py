import pandas as pd
import math
from .policy import recommend
def run_replay(daily,config,policies=('baseline','adaptive')):
    config=dict(config)
    if daily.empty or daily.sku.nunique()!=1 or daily.date.duplicated().any():
        raise ValueError('每次回放须提供单一商品且日期不能重复')
    if not policies or len(set(policies))!=len(policies) or any(p not in ('baseline','adaptive') for p in policies):
        raise ValueError('请选择不重复的有效补货策略')
    for key,minimum,default in [('initial_stock',0,None),('lead_days',1,None),('pack_size',1,1),('min_order',0,0)]:
        try:
            value=config.get(key,default)
            if pd.api.types.is_bool(value): raise ValueError()
            number=float(value)
            if not math.isfinite(number) or not number.is_integer() or number<minimum: raise ValueError()
            config[key]=int(number)
        except (ValueError,TypeError,OverflowError):
            raise ValueError(f'{key}须为不小于{minimum}的整数') from None
    try:
        start=pd.Timestamp(config['start']);end=pd.Timestamp(config['end'])
        if pd.isna(start) or pd.isna(end) or start>end: raise ValueError()
    except (KeyError,ValueError,TypeError):
        raise ValueError('请选择有效的起止日期，结束日期不得早于开始日期') from None
    demand=pd.to_numeric(daily.sold_qty,errors='coerce')
    if (demand.dropna()<0).any() or not demand.dropna().map(math.isfinite).all():
        raise ValueError('需求须为有限非负数')
    daily=daily.assign(sold_qty=demand)
    rows=[]
    for policy in policies:
        hand=int(config['initial_stock']); queue=[]
        for date in pd.date_range(config['start'],config['end']):
            today=daily[daily.date==date]
            if len(today)!=1 or today.sold_qty.isna().any(): raise ValueError('测试期必须有连续已确认覆盖的需求')
            arrived=sum(q for d,q in queue if d==date);hand+=arrived;queue=[(d,q) for d,q in queue if d>date]
            stock=dict(sku=str(daily.sku.iloc[0]),as_of=str(date.date()),on_hand=hand,on_order=sum(q for d,q in queue),lead_days=config['lead_days'],pack_size=config.get('pack_size',1),min_order=config.get('min_order',0),arrival_confirmed=True,source='回放模拟')
            r=recommend(daily,stock,dict(config,policy=policy),date); order=r['qty'] or 0
            if order: queue.append((date+pd.Timedelta(days=config['lead_days']),order))
            demand=float(today.sold_qty.iloc[0]); shortage=max(0,demand-hand);hand=max(0,hand-demand)
            rows.append(dict(policy=policy,date=date,demand=demand,arrived=arrived,order=order,ending_stock=hand,shortage=shortage,on_order=sum(q for d,q in queue),review=r['qty'] is None))
    timeline=pd.DataFrame(rows);metrics=[]
    for p,g in timeline.groupby('policy'):
        total=g.demand.sum();metrics.append(dict(policy=p,fill_rate=1-g.shortage.sum()/total if total else None,mean_stock=g.ending_stock.mean(),shortage=g.shortage.sum(),stockout_rate=(g.shortage>0).mean(),review_rate=g.review.mean(),orders=int((g.order>0).sum()),final_pipeline=g.on_order.iloc[-1]))
    eligible=timeline.pivot(index='date',columns='policy',values='review').eq(False).all(axis=1).mean()
    return timeline,pd.DataFrame(metrics).assign(common_eligible_rate=eligible)
