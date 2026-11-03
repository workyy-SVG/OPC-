from pathlib import Path
from streamlit.testing.v1 import AppTest


def click(app, label):
    next(b for b in app.button if b.label == label).click().run(timeout=60)
    assert not app.exception


def test_demo_replay_and_actual_purchase_gate():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py', default_timeout=60).run()
    assert not app.exception
    click(app, '用示例立即体验')
    assert app.session_state['navigation'] == '补货清单'
    assert len(app.session_state['inventory']) == 20
    assert any(m.label == '建议下单' and int(m.value) > 0 for m in app.metric)
    app.radio(key='usage_mode').set_value('实际采购准备').run()
    assert not app.exception
    assert any(m.label == '人工复核' and int(m.value) == 20 for m in app.metric)
    app.radio(key='navigation').set_value('策略回放').run()
    click(app, '运行双策略回放')
    timeline, metrics, config, sku = app.session_state['replay_result']
    assert timeline.demand.sum() > 0
    assert metrics.fill_rate.notna().all()


def test_bulk_changes_invalidate_verification():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py', default_timeout=60).run()
    click(app, '用示例立即体验')
    app.radio(key='navigation').set_value('库存配置').run()
    sku = app.session_state['inventory'].iloc[0].sku
    next(w for w in app.multiselect if w.label == '选择同一供应商或采购条件的商品').set_value([sku])
    next(w for w in app.number_input if w.label == '统一交期（天）').set_value(7)
    click(app, '应用到所选商品')
    row = app.session_state['inventory'].set_index('sku').loc[sku]
    assert row.lead_days == 7
    assert not row.verified
