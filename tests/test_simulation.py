from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from core.simulation import create, step, report, export_json
from core.replay import run_replay


def history():
    return pd.DataFrame(dict(sku='001',date=pd.date_range('2025-01-01',periods=120),sold_qty=5,coverage_status='confirmed'))


def test_incremental_simulation_matches_existing_replay_and_conserves_stock():
    daily=history()
    state=create(daily,'2025-04-01','2025-04-10',2,3,6)
    original=deepcopy(state)
    for _ in range(10):
        state=step(state,daily)
    assert original['timeline']==[] and original['on_hand']==2
    assert state['completed']
    timeline, _=run_replay(daily,state['config'],policies=('adaptive',))
    assert [r['ending_stock'] for r in state['timeline']]==timeline.ending_stock.tolist()
    assert [r['order'] for r in state['timeline']]==timeline.order.tolist()
    assert [r['arrived'] for r in state['timeline']]==timeline.arrived.tolist()
    assert state['on_hand']==2+sum(r['arrived'] for r in state['timeline'])-sum(r['fulfilled'] for r in state['timeline'])
    assert all(m['after']==m['before']+m['delta'] and m['after']>=0 for m in state['movements'])
    assert all(p['received_at'] is None or pd.Timestamp(p['received_at'])-pd.Timestamp(p['ordered_at'])==pd.Timedelta(days=3) for p in state['purchases'])
    assert step(state,daily)==state
    assert json.loads(export_json(state))['summary']['days']==10


def test_future_sales_never_reach_first_decision():
    daily=history()
    state=create(daily,'2025-04-01','2025-04-03')
    first=step(state,daily)['decisions'][0]
    changed=daily.copy();changed.loc[changed.date>='2025-04-01','sold_qty']=1000
    second=step(create(changed,'2025-04-01','2025-04-03'),changed)['decisions'][0]
    assert first==second
    with pytest.raises(ValueError,match='数据已变化'): step(state,changed)


def test_no_history_needs_review_and_zero_demand_has_no_fill_rate():
    daily=history();daily['sold_qty']=0
    state=create(daily,'2025-01-01','2025-01-01')
    state=step(state,daily)
    assert state['timeline'][0]['review'] and not state['purchases']
    assert report(state)['fill_rate'] is None


@pytest.mark.parametrize('field,value',[('sold_qty',-1),('sold_qty',float('inf')),('sold_qty',2.5),('coverage_status','unknown')])
def test_bad_daily_data_rejected(field,value):
    daily=history();daily[field]=value
    with pytest.raises(ValueError):create(daily,'2025-04-01','2025-04-02')


def test_end_date_pending_arrivals_are_not_received_early():
    state=step(create(history(),'2025-04-01','2025-04-01',0,3,1),history())
    assert state['completed'] and state['queue']
    assert all(p['status']=='模拟在途' for p in state['purchases'])
    assert report(state)['ending_stock']==0


def test_ui_manual_auto_pause_and_inventory_isolation():
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=60).run()
    next(b for b in app.button if b.label=='用示例立即体验').click().run()
    inv=app.session_state.inventory.copy(deep=True)
    app.radio(key='navigation').set_value('模拟经营').run()
    next(b for b in app.button if b.label=='创建或重置模拟').click().run()
    assert not app.exception
    assert not app.session_state.simulation['timeline']
    next(b for b in app.button if b.label=='逐日推进').click().run()
    assert not app.exception
    assert len(app.session_state.simulation['timeline'])==1
    app.run()
    assert len(app.session_state.simulation['timeline'])==1
    next(b for b in app.button if b.label=='自动运行').click().run()
    assert app.session_state.simulation_running
    assert len(app.session_state.simulation['timeline'])>=2
    next(b for b in app.button if b.label=='暂停模拟').click().run()
    assert not app.session_state.simulation_running
    paused=len(app.session_state.simulation['timeline'])
    app.run()
    assert len(app.session_state.simulation['timeline'])==paused
    pd.testing.assert_frame_equal(inv,app.session_state.inventory)
    next(b for b in app.button if b.label=='创建或重置模拟').click().run()
    assert not app.session_state.simulation['timeline']
