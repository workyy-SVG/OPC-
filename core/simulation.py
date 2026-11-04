"""Isolated, incremental historical trading simulation; no live API or stock writes."""
from copy import deepcopy
import hashlib
import json

import pandas as pd

from .policy import recommend, VERSION
from .purchasing import day, integer


def fingerprint(daily):
    columns = ['sku', 'date', 'sold_qty']
    if 'coverage_status' in daily:
        columns.append('coverage_status')
    return hashlib.sha256(daily[columns].sort_values(['sku', 'date']).to_csv(index=False).encode()).hexdigest()


def create(daily, start, end, initial_stock=20, lead_days=3, pack_size=1, policy='adaptive'):
    if daily.empty or daily.sku.nunique() != 1 or daily.date.duplicated().any():
        raise ValueError('模拟须选择单一商品，且每日只能有一条汇总')
    start, end = day(start), day(end)
    if start > end or (end - start).days >= 366:
        raise ValueError('请选择有效日期范围，一次最多模拟366天')
    if policy not in ('adaptive', 'baseline'):
        raise ValueError('请选择有效补货规则')
    demand = pd.to_numeric(daily.sold_qty, errors='coerce')
    if (demand.dropna() < 0).any() or not demand.dropna().map(lambda v: float(v) < float('inf') and float(v).is_integer()).all():
        raise ValueError('模拟销量须为有限非负整数')
    selected = daily.set_index('date').reindex(pd.date_range(start, end))
    if selected.sold_qty.isna().any():
        raise ValueError('模拟期间须有连续、已确认覆盖的销量')
    if 'coverage_status' in selected and not selected.coverage_status.eq('confirmed').all():
        raise ValueError('模拟期间包含未确认覆盖日期')
    config = dict(start=str(start.date()), end=str(end.date()), initial_stock=integer(initial_stock),
                  lead_days=integer(lead_days, 1), pack_size=integer(pack_size, 1), policy=policy)
    return dict(schema=1, sku=str(daily.sku.iloc[0]), data_hash=fingerprint(daily), config=config,
                next_date=config['start'], on_hand=config['initial_stock'], queue=[], purchases=[],
                timeline=[], movements=[], decisions=[], completed=False, rule_version=VERSION,
                evidence_type='历史数据驱动的模拟经营，不是真实店铺实时接口')


def step(state, daily):
    if state['data_hash'] != fingerprint(daily):
        raise ValueError('数据已变化，请重新创建模拟，避免混用结果')
    if state['completed']:
        return deepcopy(state)
    result = deepcopy(state)
    date = day(result['next_date'])
    cfg = result['config']
    sku = result['sku']
    today = daily[daily.date == date]
    if len(today) != 1 or pd.isna(today.sold_qty.iloc[0]):
        raise ValueError('当日销量缺失，模拟已停止')
    before = result['on_hand']
    received = [order for order in result['queue'] if day(order['due']) == date]
    arrived = sum(order['qty'] for order in received)
    result['on_hand'] += arrived
    result['queue'] = [order for order in result['queue'] if day(order['due']) > date]
    for purchase in result['purchases']:
        if any(order['id'] == purchase['id'] for order in received):
            purchase['status'] = '模拟已到货'
            purchase['received_at'] = str(date.date())
    if arrived:
        result['movements'].append(dict(date=str(date.date()), type='模拟采购入库', delta=arrived,
                                        before=before, after=result['on_hand']))
    stock = dict(sku=sku, as_of=str(date.date()), on_hand=result['on_hand'],
                 on_order=sum(o['qty'] for o in result['queue']), lead_days=cfg['lead_days'],
                 pack_size=cfg['pack_size'], min_order=0, arrival_confirmed=True, source='模拟经营参数')
    # Deliberately do not expose today's or future sales to the decision.
    decision = recommend(daily[daily.date < date], stock, {'policy': cfg['policy']}, date)
    result['decisions'].append(decision)
    order = decision['qty'] or 0
    if order:
        purchase = dict(id=f"SIM-{sku}-{date.date()}", ordered_at=str(date.date()),
                        due=str((date + pd.Timedelta(days=cfg['lead_days'])).date()),
                        qty=order, status='模拟在途', received_at=None)
        result['purchases'].append(purchase)
        result['queue'].append(dict(purchase))
    demand = integer(today.sold_qty.iloc[0])
    sold = min(result['on_hand'], demand)
    opening = result['on_hand']
    result['on_hand'] -= sold
    result['movements'].append(dict(date=str(date.date()), type='历史销量驱动的模拟出库',
                                    delta=-sold, before=opening, after=result['on_hand']))
    result['timeline'].append(dict(date=str(date.date()), opening_stock=before, arrived=arrived,
                                   historical_demand=demand, fulfilled=sold, shortage=demand-sold,
                                   order=order, ending_stock=result['on_hand'],
                                   on_order=sum(o['qty'] for o in result['queue']),
                                   review=decision['qty'] is None))
    result['completed'] = date >= day(cfg['end'])
    result['next_date'] = str((date + pd.Timedelta(days=1)).date())
    return result


def report(state):
    timeline = state['timeline']
    demand = sum(row['historical_demand'] for row in timeline)
    fulfilled = sum(row['fulfilled'] for row in timeline)
    return dict(days=len(timeline), historical_demand=demand, fulfilled=fulfilled,
                shortage=demand-fulfilled, fill_rate=fulfilled/demand if demand else None,
                ending_stock=state['on_hand'], on_order=sum(o['qty'] for o in state['queue']),
                review_days=sum(row['review'] for row in timeline))


def export_json(state):
    return json.dumps(dict(state, summary=report(state)), ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
