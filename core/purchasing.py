"""Dated purchase ledger and deterministic daily inventory projection."""
import json
import math
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pandas as pd


def integer(value, minimum=0):
    if isinstance(value, bool):
        raise ValueError('数量须为整数')
    number = float(value)
    if not math.isfinite(number) or not number.is_integer() or number < minimum:
        raise ValueError(f'数量须为不小于{minimum}的整数')
    return int(number)


def day(value):
    date = pd.Timestamp(value)
    if pd.isna(date) or date.tzinfo is not None:
        raise ValueError('请填写有效的本地日期')
    return date.normalize()


def add_purchase(ledger, sku, qty, ordered_at, expected_at, reason, suggested_qty=None):
    ordered, expected = day(ordered_at), day(expected_at)
    if expected < ordered:
        raise ValueError('预计到货不能早于采购日期')
    if not str(sku).strip() or not str(reason).strip():
        raise ValueError('商品和确认或修改原因不能为空')
    record = dict(id=uuid4().hex, sku=str(sku), qty=integer(qty, 1),
                  ordered_at=str(ordered.date()), expected_at=str(expected.date()),
                  reason=str(reason).strip(), suggested_qty=suggested_qty, receipts=[])
    return [*ledger, record]


def receive(ledger, purchase_id, qty, received_at):
    updated = json.loads(json.dumps(ledger))
    for record in updated:
        if record['id'] == purchase_id:
            quantity = integer(qty, 1)
            date = day(received_at)
            if date < day(record['ordered_at']):
                raise ValueError('实际到货不能早于采购日期')
            if quantity > record['qty'] - sum(r['qty'] for r in record['receipts']):
                raise ValueError('到货数量超过剩余采购数量')
            record['receipts'].append(dict(qty=quantity, date=str(date.date())))
            return updated
    raise ValueError('找不到采购记录')


def project(ledger, sku, on_hand, as_of, mean, horizon=14, accounted_receipts=()):
    start = day(as_of)
    stock = float(integer(on_hand))
    mean = float(mean)
    if not math.isfinite(mean) or mean < 0:
        raise ValueError('预测日销量须为有限非负数')
    horizon = integer(horizon, 1)
    arrivals = {}
    overdue = 0
    for record in ledger:
        if record['sku'] != sku or day(record['ordered_at']) > start:
            continue
        # Snapshot is opening stock; receipts before it are already in on_hand.
        received = sum(r['qty'] for r in record['receipts'])
        for receipt in record['receipts']:
            date = day(receipt['date'])
            if date >= start and receipt.get('receipt_id') not in accounted_receipts:
                arrivals[date] = arrivals.get(date, 0) + receipt['qty']
        outstanding = record['qty'] - received
        due = day(record['expected_at'])
        if due < start:
            overdue += outstanding  # Delayed goods are not assumed to arrive today.
        else:
            arrivals[due] = arrivals.get(due, 0) + outstanding
    rows = []
    for date in pd.date_range(start, periods=horizon):
        arrived = arrivals.get(date, 0)
        opening = stock + arrived
        shortage = max(0., mean - opening)
        stock = max(0., opening - mean)
        rows.append(dict(date=str(date.date()), arrival=arrived, demand=mean,
                         ending_stock=stock, shortage=shortage))
    return pd.DataFrame(rows), overdue


def save_ledger(path, ledger):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(dict(schema=1, purchases=ledger), stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_ledger(data):
    payload = json.loads(data)
    if not isinstance(payload, dict) or payload.get('schema') != 1 or not isinstance(payload.get('purchases'), list):
        raise ValueError('采购备份格式不正确')
    ledger = []
    ids = set()
    try:
        for record in payload['purchases']:
            if not isinstance(record['id'], str) or not record['id'] or record['id'] in ids:
                raise ValueError('采购编号为空或重复')
            ids.add(record['id'])
            validated = add_purchase([], record['sku'], record['qty'], record['ordered_at'],
                                     record['expected_at'], record['reason'], record.get('suggested_qty'))[0]
            validated['id'] = record['id']
            if not isinstance(record['receipts'], list):
                raise ValueError('到货记录格式不正确')
            current = [validated]
            for receipt in record['receipts']:
                current = receive(current, validated['id'], receipt['qty'], receipt['date'])
            for original, restored in zip(record['receipts'], current[0]['receipts']):
                if 'receipt_id' in original:
                    restored['receipt_id'] = str(original['receipt_id'])
            ledger.append(current[0])
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError('采购备份缺少必需字段或字段格式不正确') from error
    return ledger


def receive_into_inventory(ledger, inventory, movements, purchase_id, qty, received_at, receipt_id):
    """Prepare one receipt transaction; callers persist before publishing state."""
    receipt_id = str(receipt_id).strip()
    if not receipt_id:
        raise ValueError('请填写收货凭证编号；同一批到货请使用相同编号')
    quantity = integer(qty, 1)
    date = day(received_at)
    existing = next((m for m in movements if m['receipt_id'] == receipt_id), None)
    if existing:
        if (existing['purchase_id'], existing['qty'], existing['date']) != (purchase_id, quantity, str(date.date())):
            raise ValueError('此收货凭证已使用，内容不一致；请核对凭证')
        return ledger, inventory.copy(deep=True), movements
    record = next((r for r in ledger if r['id'] == purchase_id), None)
    if record is None:
        raise ValueError('找不到采购记录')
    indices = inventory.index[inventory.sku == record['sku']]
    if len(indices) != 1:
        raise ValueError('该商品缺少唯一库存配置，请先核对库存')
    idx = indices[0]
    if day(inventory.loc[idx, 'as_of']) != date:
        raise ValueError('到货日期须与当前库存日期一致，请先完成该日库存核对，避免跨日遗漏销售')
    updated = receive(ledger, purchase_id, quantity, date)
    next(r for r in updated if r['id'] == purchase_id)['receipts'][-1]['receipt_id'] = receipt_id
    result = inventory.copy(deep=True)
    before = integer(result.loc[idx, 'on_hand'])
    result.loc[idx, 'on_hand'] = before + quantity
    result.loc[idx, 'on_order'] = max(0, integer(result.loc[idx, 'on_order']) - quantity)
    result.loc[idx, 'verified'] = False
    movement = dict(receipt_id=receipt_id, purchase_id=purchase_id, sku=record['sku'],
                    date=str(date.date()), qty=quantity, before=before, after=before + quantity)
    return updated, result, [*movements, movement]


def save_workspace(path, ledger, inventory, movements):
    """Atomic snapshot of purchases, inventory and receipt movements together."""
    payload = dict(schema=2, purchases=ledger, inventory=json.loads(inventory.to_json(orient='records')),
                   movements=movements)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_workspace(data):
    from .inventory import parse_inventory
    payload = json.loads(data)
    if not isinstance(payload, dict) or payload.get('schema') != 2:
        raise ValueError('请使用包含库存与流水的新版完整备份')
    ledger = load_ledger(json.dumps(dict(schema=1, purchases=payload.get('purchases'))))
    columns = ['sku','as_of','on_hand','on_order','lead_days','pack_size','min_order',
               'arrival_confirmed','source','parameter_kind','verified']
    inv = parse_inventory(pd.DataFrame(payload.get('inventory', [])), columns)
    movements = payload.get('movements')
    if not isinstance(movements, list):
        raise ValueError('库存流水格式不正确')
    ids = set()
    try:
        for m in movements:
            key = m['receipt_id']
            if not isinstance(key, str) or not key or key in ids:
                raise ValueError('收货凭证为空或重复')
            ids.add(key)
            qty = integer(m['qty'], 1)
            if integer(m['after']) != integer(m['before']) + qty:
                raise ValueError('库存流水数量不一致')
            record = next((r for r in ledger if r['id'] == m['purchase_id'] and r['sku'] == m['sku']), None)
            if record is None or not any(r.get('receipt_id') == key and r['qty'] == qty and r['date'] == m['date'] for r in record['receipts']):
                raise ValueError('库存流水与到货记录不一致')
            day(m['date'])
    except (KeyError, TypeError) as error:
        raise ValueError('库存流水字段不完整') from error
    receipt_ids = [r['receipt_id'] for p in ledger for r in p['receipts'] if 'receipt_id' in r]
    if len(receipt_ids) != len(set(receipt_ids)) or set(receipt_ids) != ids:
        raise ValueError('到货凭证与库存流水不一致')
    return ledger, inv, movements
