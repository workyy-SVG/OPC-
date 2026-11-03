import pandas as pd
from core.study import frontier,paired_ci,run_study
from core.ingest import parse_orders
from core.demand import build_daily

def test_frontier():
 d=pd.DataFrame({'mean_stock':[1,2,3,4],'fill_rate':[.5,.7,.6,.8]})
 assert frontier(d).index.tolist()==[0,1,3]
def test_ci():
 c=paired_ci([1,2,3],[0,1,2]);assert c['low']==c['high']==1
 assert paired_ci([1],[0])['low'] is None
def test_study():
 d=pd.DataFrame({'sku':['a']*100,'date':pd.date_range('2025-01-01',periods=100),'sold_qty':[5]*100})
 r=run_study(d,dict(start='2025-04-01',end='2025-04-03',lead_days=3,initial_stock=20),ratios=(.2,.4),scales=(1,))
 assert len(r['summary'])==3 and len(r['oracle'])==1
 assert r['per_sku'].cost.ge(0).all()
