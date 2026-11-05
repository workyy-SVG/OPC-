from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / 'app.py'


def click(app, label):
    next(button for button in app.button if button.label == label).click().run()
    assert not app.exception


@pytest.mark.parametrize('code,unit', [('CNY','元'),('USD','美元'),('GBP','英镑'),('EUR','欧元')])
def test_import_currency_labels_preview_and_survives_navigation(code,unit):
    app = AppTest.from_file(APP, default_timeout=30).run()
    app.radio(key='navigation').set_value('导入与质检').run()
    raw = b'order_id,sku,order_at,qty,unit_price\n001,001,2025-01-01,2,3.50\n'
    next(w for w in app.get('file_uploader') if w.label == '上传销售 CSV').upload('prices.csv',raw).run()
    app.selectbox(key='sales_import_currency').set_value(code).run()
    assert any(f'单价（{unit}/件）' in item.value for item in app.markdown)
    assert any('3.50' in item.value for item in app.markdown)
    app.checkbox(key='sales_coverage_confirmed').check().run()
    click(app,'核验并使用此数据')
    assert app.session_state.sales_currency == code
    assert app.session_state.raw == raw
    click(app,'下一步：核对库存 →')
    assert app.session_state.navigation == '库存配置'
    assert app.session_state.sales_currency == code
    app.radio(key='navigation').set_value('公平实验室').run()
    assert not app.exception
    assert any(w.label == f'每件每天持有成本（{unit}，假设值）' for w in app.number_input)


def test_uci_prices_keep_original_pounds():
    app = AppTest.from_file(APP, default_timeout=30).run()
    app.radio(key='navigation').set_value('导入与质检').run()
    click(app,'一键加载 UCI 真实交易样本')
    selector = app.selectbox(key='sales_import_currency')
    assert selector.value == 'GBP' and selector.disabled
    assert any('单价（英镑/件）' in item.value for item in app.markdown)
    app.checkbox(key='sales_coverage_confirmed').check().run()
    click(app,'核验并使用此数据')
    assert app.session_state.sales_currency == 'GBP'


def test_scroll_reset_only_on_page_transition_including_empty_pages():
    app = AppTest.from_file(APP, default_timeout=30).run()
    assert len(app.get('html')) == 1
    app.run()
    assert len(app.get('html')) == 0
    app.radio(key='navigation').set_value('库存配置').run()
    assert not app.exception
    assert len(app.get('html')) == 1
    app.run()
    assert len(app.get('html')) == 0
