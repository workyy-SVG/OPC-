import json

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from pathlib import Path

from core.purchasing import add_purchase, receive, project, save_ledger, load_ledger


def purchase():
    return add_purchase([], '001', 10, '2025-04-01', '2025-04-03', '核对后采购', 12)


def test_partial_receipt_preserves_audit_and_limits():
    original = purchase()
    ledger = receive(original, original[0]['id'], 4, '2025-04-02')
    assert not original[0]['receipts']
    assert ledger[0]['suggested_qty'] == 12
    assert ledger[0]['reason'] == '核对后采购'
    with pytest.raises(ValueError): receive(ledger, ledger[0]['id'], 7, '2025-04-03')
    with pytest.raises(ValueError): receive(ledger, ledger[0]['id'], 1, '2025-03-31')


def test_daily_projection_dated_arrivals_and_lost_sales():
    ledger = purchase()
    ledger = receive(ledger, ledger[0]['id'], 4, '2025-04-02')
    projection, overdue = project(ledger, '001', 2, '2025-04-01', 3, 3)
    assert projection.arrival.tolist() == [0, 4, 6]
    assert projection.shortage.tolist() == [1, 0, 0]
    assert projection.ending_stock.tolist() == [0, 1, 4]
    assert overdue == 0


def test_overdue_and_snapshot_do_not_double_count():
    ledger = purchase()
    ledger = receive(ledger, ledger[0]['id'], 4, '2025-04-02')
    projection, overdue = project(ledger, '001', 4, '2025-04-04', 1, 2)
    assert overdue == 6
    assert projection.arrival.sum() == 0
    assert projection.ending_stock.tolist() == [3, 2]
    assert project(ledger, 'other', 0, '2025-04-04', 1, 2)[1] == 0


def test_atomic_save_restore_and_corrupt_backup(tmp_path):
    ledger = purchase(); path = tmp_path / 'ledger.json'
    save_ledger(path, ledger)
    assert load_ledger(path.read_bytes()) == ledger
    save_ledger(path, receive(ledger, ledger[0]['id'], 10, '2025-04-03'))
    assert load_ledger(path.read_bytes())[0]['receipts'][0]['qty'] == 10
    bad = dict(schema=1, purchases=ledger + ledger)
    with pytest.raises(ValueError): load_ledger(json.dumps(bad))


@pytest.mark.parametrize('qty', [0, -1, 1.5, float('inf'), True])
def test_invalid_purchase_quantities(qty):
    with pytest.raises(ValueError): add_purchase([], '001', qty, '2025-04-01', '2025-04-03', '确认')


def test_purchase_page_create_and_receive():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py', default_timeout=60).run()
    next(b for b in app.button if b.label == '用示例立即体验').click().run()
    app.radio(key='navigation').set_value('采购与到货').run()
    assert not app.exception
    next(w for w in app.text_input if w.label == '确认或修改原因').set_value('测试采购')
    next(w for w in app.checkbox if w.label.startswith('我已核对库存')).check()
    next(b for b in app.button if b.label == '登记确认采购').click().run()
    assert not app.exception
    assert len(app.session_state.purchases) == 1
    sku = app.session_state.purchases[0]['sku']
    before = int(app.session_state.inventory.set_index('sku').loc[sku, 'on_hand'])
    next(w for w in app.text_input if w.label == '收货凭证编号').set_value('receipt-ui-001')
    next(b for b in app.button if b.label == '保存到货记录').click().run()
    assert not app.exception
    assert app.session_state.purchases[0]['receipts'][0]['qty'] == 1
    assert int(app.session_state.inventory.set_index('sku').loc[sku, 'on_hand']) == before + 1
    assert len(app.session_state.stock_movements) == 1
    app.run()
    assert int(app.session_state.inventory.set_index('sku').loc[sku, 'on_hand']) == before + 1


@pytest.mark.parametrize('data', ['[]', 'null', '{"schema":1,"purchases":[null]}', '{"schema":1,"purchases":[{}]}'])
def test_malformed_backup_is_rejected(data):
    with pytest.raises(ValueError): load_ledger(data)

from core.purchasing import receive_into_inventory, save_workspace, load_workspace


def inventory_for_receipt():
    return pd.DataFrame([dict(sku='001', as_of='2025-04-02', on_hand=5, on_order=10,
        lead_days=3, pack_size=1, min_order=0, arrival_confirmed=True,
        source='盘点记录', parameter_kind='实际盘点', verified=True)])


def test_receive_updates_inventory_once_and_backup_roundtrip(tmp_path):
    original = purchase(); inventory = inventory_for_receipt()
    ledger, inv, movements = receive_into_inventory(original, inventory, [], original[0]['id'], 4, '2025-04-02', 'R1')
    assert inv.iloc[0].on_hand == 9 and inv.iloc[0].on_order == 10
    assert not inv.iloc[0].verified
    assert inventory.iloc[0].on_hand == 5 and not original[0]['receipts']
    repeated, inv2, moves2 = receive_into_inventory(ledger, inv, movements, original[0]['id'], 4, '2025-04-02', 'R1')
    assert inv2.iloc[0].on_hand == 9 and len(moves2) == 1
    with pytest.raises(ValueError):
        receive_into_inventory(ledger, inv, movements, original[0]['id'], 3, '2025-04-02', 'R1')
    path = tmp_path / 'workspace.json'
    save_workspace(path, repeated, inv2, moves2)
    restored, restored_inv, restored_moves = load_workspace(path.read_bytes())
    assert restored == ledger and restored_moves == movements
    assert restored_inv.iloc[0].on_hand == 9
    projection, _ = project(restored, '001', 9, '2025-04-02', 1, 1, {'R1'})
    assert projection.iloc[0].arrival == 0 and projection.iloc[0].ending_stock == 8
    _, final, final_moves = receive_into_inventory(restored, restored_inv, restored_moves, original[0]['id'], 6, '2025-04-02', 'R2')
    assert final.iloc[0].on_hand == 15 and final.iloc[0].on_order == 10
    assert len(final_moves) == 2


@pytest.mark.parametrize('date,sku', [('2025-04-03','001'),('2025-04-02','other')])
def test_receipt_rejects_mismatched_snapshot_or_product(date, sku):
    inv = inventory_for_receipt(); inv.loc[0,'sku'] = sku
    ledger = purchase()
    with pytest.raises(ValueError):
        receive_into_inventory(ledger, inv, [], ledger[0]['id'], 1, date, 'R1')
    assert inv.iloc[0].on_hand == 5 and not ledger[0]['receipts']


def test_workspace_failed_replace_preserves_saved_state(tmp_path, monkeypatch):
    import core.purchasing as purchasing
    path = tmp_path / 'workspace.json'
    save_workspace(path, purchase(), inventory_for_receipt(), [])
    before = path.read_bytes()
    def fail(*args): raise OSError('disk failure')
    monkeypatch.setattr(purchasing.os, 'replace', fail)
    with pytest.raises(OSError): save_workspace(path, [], inventory_for_receipt(), [])
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('*.tmp'))
