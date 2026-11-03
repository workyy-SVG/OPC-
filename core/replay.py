import pandas as pd
from .policy import recommend
def run_replay(daily,config,policies=('baseline','adaptive')):
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
