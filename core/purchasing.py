"""Dated purchase ledger and deterministic daily inventory projection."""
import json
import math
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pandas as pd
from .numbers import integer, MAX_QUANTITY


def day(value):
    date = pd.Timestamp(value)
    if pd.isna(date) or date.tzinfo is not None:
        raise ValueError('请填写有效的本地日期')
    return date.normalize()


def add_purchase(ledger, sku, qty, ordered_at, expected_at, reason, suggested_qty=None, request_id=None):
    ordered, expected = day(ordered_at), day(expected_at)
    if expected < ordered:
        raise ValueError('预计到货不能早于采购日期')
    if not str(sku).strip() or not str(reason).strip():
        raise ValueError('商品和确认或修改原因不能为空')
    record = dict(id=request_id or uuid4().hex, sku=str(sku).strip(), qty=integer(qty, 1),
                  ordered_at=str(ordered.date()), expected_at=str(expected.date()),
                  reason=str(reason).strip(), suggested_qty=suggested_qty, receipts=[])
    existing = next((r for r in ledger if r['id'] == record['id']), None)
    if existing:
        if any(existing[k] != record[k] for k in ('sku','qty','ordered_at','expected_at','reason')):
            raise ValueError('采购提交编号已使用且内容不同')
        return ledger
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
    result.loc[idx, 'on_hand'] = integer(before + quantity)
    # on_order represents external unitemized stock, never this purchase.
    result.loc[idx, 'verified'] = False
    movement = dict(receipt_id=receipt_id, purchase_id=purchase_id, sku=record['sku'],
                    date=str(date.date()), qty=quantity, before=before, after=before + quantity)
    return updated, result, [*movements, movement]


def validate_pipeline(inventory,ledger):
    products=set(inventory.sku) if 'sku' in inventory else set()
    if any(record['sku'] not in products for record in ledger):
        raise ValueError('采购商品缺少对应库存配置')
    for _,row in inventory.iterrows():
        pending=sum(record['qty']-sum(receipt['qty'] for receipt in record['receipts'])
                    for record in ledger if record['sku']==row['sku'])
        integer(integer(row['on_order'])+pending)


def save_workspace(path, ledger, inventory, movements, source_hash=None, sales_updates=None, expected_revision=None):
    """Atomic snapshot with optimistic revision checking for recovered sessions."""
    validate_pipeline(inventory,ledger)
    path = Path(path)
    from . import storage
    if storage.remote_enabled():
        payload=dict(schema=2,pipeline_semantics='external-only-v1',source_hash=source_hash,sales_updates=sales_updates or [],purchases=ledger,
                     inventory=json.loads(inventory.to_json(orient='records')),movements=movements)
        revision=storage.rpc('opc_workspace_save',dict(p_id=path.stem,p_payload=payload,p_expected_revision=expected_revision if expected_revision is not None else 0))
        if not isinstance(revision,int) or isinstance(revision,bool) or revision<1:
            raise storage.StorageError('数据库保存未确认，请恢复最新记录')
        return revision
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = Path(str(path)+'.lock')
    try:
        lock_fd = os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:
        raise ValueError('工作区正在写入，请稍后重试') from None
    temporary = None
    try:
        current = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        revision = current.get('revision',0)
        if expected_revision is not None and expected_revision != revision:
            raise ValueError('工作区已在其他会话更新，请先恢复最新记录，未覆盖已有数据')
        payload = dict(schema=2, pipeline_semantics='external-only-v1', revision=revision+1, source_hash=source_hash, sales_updates=sales_updates or [],
                       purchases=ledger, inventory=json.loads(inventory.to_json(orient='records')), movements=movements)
        fd, temporary = tempfile.mkstemp(dir=path.parent,suffix='.tmp')
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(payload,stream,ensure_ascii=False,indent=2,allow_nan=False)
        os.replace(temporary,path)
        return revision+1
    finally:
        if temporary and os.path.exists(temporary): os.unlink(temporary)
        os.close(lock_fd)
        lock.unlink(missing_ok=True)


def load_workspace(data):
    from .inventory import parse_inventory
    payload = json.loads(data)
    if not isinstance(payload, dict) or payload.get('schema') != 2:
        raise ValueError('请使用包含库存与流水的新版完整备份')
    ledger = load_ledger(json.dumps(dict(schema=1, purchases=payload.get('purchases'))))
    columns = ['sku','as_of','on_hand','on_order','lead_days','pack_size','min_order',
               'arrival_confirmed','source','parameter_kind','verified']
    inv = parse_inventory(pd.DataFrame(payload.get('inventory', [])), columns)
    if any(p['sku'] not in set(inv.sku) for p in ledger):
        raise ValueError('采购商品缺少对应库存配置')
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
    updates=payload.get('sales_updates',[])
    if not isinstance(updates,list): raise ValueError('销售更新流水格式不正确')
    try:
        for update in updates:
            if update['sku'] not in set(inv.sku) or day(update['start'])>day(update['end']):
                raise ValueError('销售更新流水商品或日期不一致')
            sold=integer(update['sold'])
            if integer(update['before'])-sold!=integer(update['after']):
                raise ValueError('销售更新流水数量不一致')
    except (TypeError,KeyError):
        raise ValueError('销售更新流水字段不完整') from None
    validate_pipeline(inv,ledger)
    return ledger, inv, movements


def effective_stock(stock, ledger, as_of):
    """External pipeline plus separately itemized purchases, shared by all pages."""
    result = dict(stock)
    date = day(as_of)
    pending = [(r, r['qty'] - sum(x['qty'] for x in r['receipts']))
               for r in ledger if r['sku'] == str(stock.get('sku')) and day(r['ordered_at']) <= date]
    pending = [(r, q) for r, q in pending if q > 0]
    external = integer(stock.get('on_order', 0))
    result['on_order'] = integer(external + sum(q for r, q in pending))
    result['external_on_order'] = external
    result['managed_on_order'] = sum(q for r, q in pending)
    result['arrival_confirmed'] = (not external or (pd.api.types.is_bool(stock.get('arrival_confirmed')) and bool(stock['arrival_confirmed']))) and all(
        date <= day(r['expected_at']) <= date + pd.Timedelta(days=integer(stock['lead_days'],1)) for r,q in pending)
    return result


def register_purchase(ledger, inventory, sku, qty, ordered_at, expected_at, reason, request_id, external=False, suggested_qty=None):
    updated = add_purchase(ledger, sku, qty, ordered_at, expected_at, reason, suggested_qty, request_id)
    result = inventory.copy(deep=True)
    if external and len(updated) != len(ledger):
        indices = result.index[result.sku == sku]
        if len(indices) != 1:
            raise ValueError('缺少唯一商品库存')
        idx = indices[0]
        remaining = integer(result.loc[idx,'on_order']) - integer(qty,1)
        if remaining < 0:
            raise ValueError('转为明细的数量不能超过外部汇总在途')
        result.loc[idx,'on_order'] = remaining
        result.loc[idx,'verified'] = False
    validate_pipeline(result,updated)
    return updated, result


def workspace_path(code):
    import re
    if not isinstance(code,str) or not re.fullmatch(r'[a-f0-9]{32}',code):
        raise ValueError('工作区恢复码格式不正确')
    from .storage import secret
    return Path(secret('WORKSPACE_STORAGE_DIR','outputs/purchases')) / (code+'.json')


def check_workspace_source(payload, source_hash, allow_legacy=False):
    if not isinstance(payload,dict): raise ValueError('备份格式不正确')
    stored = payload.get('source_hash')
    if not stored and not allow_legacy:
        raise ValueError('旧备份没有来源标识，请确认来源后勾选兼容恢复')
    if stored and stored != source_hash:
        raise ValueError('备份与当前销售数据或覆盖范围不一致，请先加载备份对应的销售文件和日期')
    if payload.get('pipeline_semantics')!='external-only-v1' and not allow_legacy:
        raise ValueError('旧备份的在途口径需要核对，请确认外部在途不含台账采购后勾选兼容恢复')


def read_workspace(code):
    path=workspace_path(code)
    from . import storage
    if storage.remote_enabled():
        result=storage.rpc('opc_workspace_read',{'p_id':code})
        if not isinstance(result,dict): raise storage.StorageError('数据库快照格式不正确')
        return json.dumps(result,ensure_ascii=False).encode()
    return path.read_bytes()

APP_REVISION = "audit-fixes-20261104"
