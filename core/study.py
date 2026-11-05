"""Exploratory test-set frontier. Never use test-set winners as deployable tuning."""
import numpy as np
import pandas as pd
from .replay import run_replay

SAME_COHORT_AXES = True

def frontier(points):
    p=points.sort_values(['mean_stock','fill_rate'],ascending=[True,False]).dropna(subset=['fill_rate'])
    keep=[];best=-1
    for i,r in p.iterrows():
        if r.fill_rate>best: keep.append(i);best=r.fill_rate
    return p.loc[keep]

def paired_ci(a,b,seed=42,repeats=2000):
    delta=np.asarray(a,dtype=float)-np.asarray(b,dtype=float)
    delta=delta[np.isfinite(delta)]
    if len(delta)<2: return dict(difference=float(delta.mean()) if len(delta) else None,low=None,high=None,n=len(delta))
    rng=np.random.default_rng(seed)
    means=delta[rng.integers(0,len(delta),(repeats,len(delta)))].mean(axis=1)
    return dict(difference=float(delta.mean()),low=float(np.quantile(means,.025)),high=float(np.quantile(means,.975)),n=len(delta))

def run_study(daily,config,ratios=(.1,.2,.3,.4,.5,.6),scales=(.6,1,1.4,1.8),holding=1.,shortage_cost=10.,order_cost=0.):
    records=[]
    for sku,g in daily.groupby('sku'):
        for policy,values in [('baseline',ratios),('adaptive',scales)]:
            for value in values:
                cfg=dict(config,baseline_ratio=value if policy=='baseline' else .2,adaptive_scale=value if policy=='adaptive' else 1)
                t,m=run_replay(g,cfg,policies=(policy,));r=m.iloc[0].to_dict()
                r.update(sku=sku,parameter=value,stock_days=float(t.ending_stock.sum()),demand=float(t.demand.sum()))
                r['cost']=r['stock_days']*holding+r['shortage']*shortage_cost+r['orders']*order_cost
                records.append(r)
    per=pd.DataFrame(records)
    # Undefined fill rates must also be excluded from the stock axis.
    comparable = per.assign(comparable_stock=per.mean_stock.where(per.fill_rate.notna()))
    summary=comparable.groupby(['policy','parameter'],as_index=False).agg(mean_stock=('comparable_stock','mean'),fill_rate=('fill_rate','mean'),cost=('cost','sum'),review_rate=('review_rate','mean'))
    summary['dominated']=[bool(((summary.mean_stock<=r.mean_stock)&(summary.fill_rate>=r.fill_rate)&((summary.mean_stock<r.mean_stock)|(summary.fill_rate>r.fill_rate))).any()) for _,r in summary.iterrows()]
    base=frontier(summary[summary.policy=='baseline']); comparisons=[]
    for _,r in summary[summary.policy=='adaptive'].iterrows():
        inside=len(base)>1 and base.mean_stock.min()<=r.mean_stock<=base.mean_stock.max()
        value=float(np.interp(r.mean_stock,base.mean_stock,base.fill_rate)) if inside else None
        comparisons.append(dict(scale=r.parameter,mean_stock=r.mean_stock,adaptive_fill=r.fill_rate,baseline_interpolated=value,difference=r.fill_rate-value if inside else None))
    # Oracle is chosen on test outcomes and is explicitly only a hindsight cost reference.
    b=per[per.policy=='baseline'];oracle=b.loc[b.groupby('sku').cost.idxmin()].copy()
    a=per[(per.policy=='adaptive')&(per.parameter==1)].set_index('sku')
    fixed=b[b.parameter==.2].set_index('sku')
    common=a.index.intersection(fixed.index)
    ci=paired_ci(a.loc[common,'fill_rate'],fixed.loc[common,'fill_rate'])
    costs=[]
    for ratio in [1,2,5,10,20,50,100]:
        q=per.assign(scenario_cost=per.stock_days*holding+per.shortage*holding*ratio+per.orders*order_cost)
        for (policy,param),g in q.groupby(['policy','parameter']):
            costs.append(dict(shortage_holding_ratio=ratio,policy=policy,parameter=param,cost=g.scenario_cost.sum()))
    return dict(per_sku=per,summary=summary,equal_inventory=pd.DataFrame(comparisons),oracle=oracle,cost_scan=pd.DataFrame(costs),ci=ci)

APP_REVISION = "audit-fixes-20261104"
