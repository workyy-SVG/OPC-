import pandas as pd
import pytest
from core.policy import recommend,order_quantity
from core.ingest import parse_orders
from core.replay import run_replay
from core.export import csv_bytes

def history(): return pd.DataFrame(dict(sku=['001']*120,date=pd.date_range('2025-01-01',periods=120),sold_qty=[5]*120))
def stock(): return dict(sku='001',as_of='2025-04-01',on_hand=8,on_order=4,lead_days=3,pack_size=6,min_order=15,arrival_confirmed=True)
def test_manual():
 assert order_quantity(5,6,12,3,15,6)==(18,26)
 assert order_quantity(5,6,26,3,15,6)==(0,26)
def test_missing():
 s=stock();s.pop('lead_days');assert recommend(history(),s,{},'2025-04-01')['qty'] is None
 h=history().drop(index=85);assert recommend(h,stock(),{},'2025-04-01')['qty'] is None
def test_future():
 h=history();a=recommend(h,stock(),{},'2025-04-01');h.loc[h.date>='2025-04-01','sold_qty']=999
 b=recommend(h,stock(),{},'2025-04-01');assert a['qty']==b['qty'];assert a['buffer']==b['buffer']
def test_ingest():
 d=pd.DataFrame([['001','002','2025-01-01','2'],['C2','002','2025-01-01','2'],['003','002','2025-01-01','-1'],['004','002','bad','2']],columns=['order_id','sku','order_at','qty'])
 c,i,s=parse_orders(d);assert c.sku.iloc[0]=='002';assert s['valid']==1;assert len(i)==3
 with pytest.raises(ValueError):parse_orders(d.drop(columns='qty'))
def test_conflict():
 d=pd.DataFrame(dict(order_id=['1','1'],sku=['01','01'],order_at=['2025-01-01']*2,qty=['2','3'],line_id=['1','1']))
 assert parse_orders(d)[0].empty
def test_replay():
 cfg=dict(start='2025-04-01',end='2025-04-10',lead_days=3,initial_stock=2)
 t,m=run_replay(history(),cfg);a=t[t.policy=='adaptive'].reset_index(drop=True)
 assert a.iloc[0].shortage==3;assert a.iloc[0].ending_stock==0;assert a.iloc[3].arrived==a.iloc[0].order
 t2,m2=run_replay(history(),cfg);pd.testing.assert_frame_equal(m,m2)
def test_export(): assert b"'=1" in csv_bytes(pd.DataFrame({'sku':['=1']}))
def test_ui():
 from streamlit.testing.v1 import AppTest
 a=AppTest.from_file('../app.py',default_timeout=60).run();assert not a.exception
 a.button[0].click().run();assert not a.exception
 a.checkbox[0].check().run();a.button[1].click().run();assert not a.exception
 for page in ['库存配置','补货清单','策略回放','使用指南','数据与说明']:
  a.sidebar.radio[0].set_value(page).run(timeout=60);assert not a.exception


 a.sidebar.radio[0].set_value('策略回放').run(timeout=60)
 a.button[0].click().run(timeout=60);assert not a.exception
 assert len(a.markdown)>2

