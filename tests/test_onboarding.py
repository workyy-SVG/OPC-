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
    assert any(m.label == '建议下单' and int(m.value) > 0 for m in app.metric)
    click(app, '继续演示')
    assert app.radio(key='usage_mode').value == '演示探索'
    app.radio(key='usage_mode').set_value('实际采购准备').run()
    click(app, '确认切换')
    assert any(m.label == '人工复核' and int(m.value) == 20 for m in app.metric)
    assert any('20 个商品缺少已核实的实际库存' in warning.value for warning in app.warning)
    app.radio(key='navigation').set_value('策略回放').run()
    click(app, '运行双策略回放')
    timeline, metrics, config, sku = app.session_state['replay_result']
    assert timeline.demand.sum() > 0
    assert metrics.fill_rate.notna().all()


def test_empty_pages_offer_next_actions_and_demo_stays_on_page():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py', default_timeout=60).run()
    for page in ('库存配置', '补货清单', '策略回放', '公平实验室', '采购与到货', '模拟经营'):
        app.radio(key='navigation').set_value(page).run()
        assert {'前往导入与质检', '加载示例数据'} <= {b.label for b in app.button}
    click(app, '加载示例数据')
    assert app.session_state['navigation'] == '模拟经营'
    app.radio(key='navigation').set_value('导入与质检').run()
    assert any('已核验' in item.value and '5,556' in item.value for item in app.success)
    assert not any(b.label == '一键加载 UCI 真实交易样本' for b in app.button)
    assert len(app.session_state['product_names']) == 20


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


def test_invalid_replay_date_shows_error_without_crashing():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py', default_timeout=60).run()
    click(app, '用示例立即体验')
    app.radio(key='navigation').set_value('策略回放').run()
    app.text_input(key='测试日期start').set_value('NaT').run()
    assert not app.exception
    assert any('开始日期不能晚于结束日期' in error.value for error in app.error)
