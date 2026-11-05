"""Validate uploaded inventory before replacing the current session's data."""
from .numbers import integer

import pandas as pd


def parse_inventory(raw, required_columns):
    inv = raw.copy().fillna('')
    if inv.empty:
        raise ValueError('库存文件不能为空')
    inv['parameter_kind'] = inv.get('parameter_kind', '未核实')
    inv['verified'] = False
    if not set(required_columns) <= set(inv.columns):
        raise ValueError('库存字段不完整，请使用下载的库存模板')
    if inv.columns.duplicated().any():
        raise ValueError('库存字段重复，请检查中文列名与机器字段名')
    inv['sku'] = inv['sku'].astype(str).str.strip()
    if inv.sku.eq('').any() or inv.sku.duplicated().any():
        raise ValueError('商品编码不能为空或重复')
    for key in ['on_hand', 'on_order', 'lead_days', 'pack_size', 'min_order']:
        minimum = 1 if key in ['lead_days', 'pack_size'] else 0
        inv[key] = [integer(value, minimum) for value in inv[key]]
    confirmations = []
    for value in inv['arrival_confirmed']:
        if pd.api.types.is_bool(value):
            confirmations.append(bool(value))
        elif str(value).strip() in ['是', '否', 'True', 'False']:
            confirmations.append(str(value).strip() in ['是', 'True'])
        else:
            raise ValueError('到货确认只能填写“是”或“否”')
    inv['arrival_confirmed'] = confirmations
    normalized_dates = []
    for value in inv['as_of']:
        try:
            date = pd.to_datetime(value, format='%Y-%m-%d', errors='raise')
            if pd.isna(date) or date.tzinfo is not None:
                raise ValueError()
            normalized_dates.append(str(date.date()))
        except (TypeError, ValueError):
            raise ValueError('库存快照日期须为有效的年-月-日') from None
    inv['as_of'] = normalized_dates
    return inv

APP_REVISION = "audit-fixes-20261104"
