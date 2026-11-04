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

@pytest.mark.parametrize('confirmation', [float('nan'), pd.NA, 'False', 'True', 1, None])
def test_arrival_requires_explicit_boolean_confirmation(confirmation):
 s=stock(); s['arrival_confirmed']=confirmation
 result=recommend(history(),s,{},'2025-04-01')
 assert result['qty'] is None
 assert 'ARRIVAL_UNCONFIRMED' in result['reason_codes']

def test_integer_csv_parameters_are_normalized_without_mutating_input():
 original=stock()
 numeric=['on_hand','on_order','lead_days','pack_size','min_order']
 uploaded={k:(str(v)+'.0' if k in numeric else v) for k,v in original.items()}
 result=recommend(history(),uploaded,{},'2025-04-01')
 assert result['qty']==recommend(history(),original,{},'2025-04-01')['qty']
 assert uploaded['lead_days']=='3.0'
 original['pack_size']=True
 assert 'INVALID_PACK_SIZE' in recommend(history(),original,{},'2025-04-01')['reason_codes']

def test_nat_order_date_is_reported_instead_of_crashing():
 raw=pd.DataFrame([dict(order_id='001',sku='002',order_at='NaT',qty='2')])
 clean,issues,summary=parse_orders(raw)
 assert clean.empty and summary['error']==1
 assert issues.iloc[0].reason_code=='INVALID_FIELD'

def test_confirmed_coverage_before_first_sale_is_included():
 from core.demand import build_daily
 raw=pd.DataFrame([dict(order_id='001',sku='002',order_at='2025-01-03',qty='2')])
 clean,_,_=parse_orders(raw)
 daily=build_daily(clean,pd.date_range('2025-01-01','2025-01-04'))
 assert daily.date.tolist()==list(pd.date_range('2025-01-01','2025-01-04'))
 assert daily.sold_qty.tolist()==[0,0,2,0]
 assert daily.coverage_status.eq('confirmed').all()

def test_replay():
 cfg=dict(start='2025-04-01',end='2025-04-10',lead_days=3,initial_stock=2)
 t,m=run_replay(history(),cfg);a=t[t.policy=='adaptive'].reset_index(drop=True)
 assert a.iloc[0].shortage==3;assert a.iloc[0].ending_stock==0;assert a.iloc[3].arrived==a.iloc[0].order
 t2,m2=run_replay(history(),cfg);pd.testing.assert_frame_equal(m,m2)

@pytest.mark.parametrize('overrides', [dict(lead_days=0),dict(initial_stock=-1),
 dict(pack_size=1.5),dict(start='2025-04-11'),dict(end='NaT')])
def test_replay_rejects_invalid_config(overrides):
 cfg=dict(start='2025-04-01',end='2025-04-10',lead_days=3,initial_stock=2)
 with pytest.raises(ValueError): run_replay(history(),cfg|overrides)

@pytest.mark.parametrize('demand', [-1,float('inf')])
def test_replay_rejects_invalid_demand(demand):
 data=history();data['sold_qty']=data.sold_qty.astype(float);data.loc[90,'sold_qty']=demand
 with pytest.raises(ValueError):
  run_replay(data,dict(start='2025-04-01',end='2025-04-10',lead_days=3,initial_stock=2))
def test_export(): assert b"'=1" in csv_bytes(pd.DataFrame({'sku':['=1']}))
def test_ui():
 from streamlit.testing.v1 import AppTest
 from pathlib import Path
 a=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=60).run();assert not a.exception
 a.sidebar.radio[0].set_value('导入与质检').run()
 a.button[0].click().run();assert not a.exception
 a.checkbox[0].check().run();a.button[1].click().run();assert not a.exception
 for page in ['库存配置','补货清单','策略回放','公平实验室','使用指南','数据与说明']:
  a.sidebar.radio[0].set_value(page).run(timeout=60);assert not a.exception


 a.sidebar.radio[0].set_value('策略回放').run(timeout=60)
 a.button[0].click().run(timeout=60);assert not a.exception
 assert len(a.markdown)>2



def test_uci_sample_provenance():
 import json,hashlib
 from pathlib import Path
 root=Path(__file__).resolve().parents[1]
 meta=json.loads((root/'data/demo/manifest.json').read_text(encoding='utf-8'))
 raw=(root/'data/demo/orders.csv').read_bytes()
 assert hashlib.sha256(raw).hexdigest()==meta['sample_sha256']
 d=pd.read_csv(root/'data/demo/orders.csv',dtype=str)
 assert len(d)==meta['rows'] and d.sku.nunique()==20
 assert 'line_id' not in d and 'Customer ID' not in d
 assert d.order_at.min()>=meta['start'] and d.order_at.max()<=meta['end']
 assert d.order_id.str.startswith('C').any()
