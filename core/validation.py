"""Scenario experiments describe sensitivity, not observed merchant outcomes."""
from itertools import product
import math
import pandas as pd


def parameter_status(stock):
    actual = stock.get('parameter_kind') == '实际盘点'
    flag = stock.get('verified')
    confirmed = pd.api.types.is_bool(flag) and bool(flag)
    source = stock.get('source','')
    evidence = source.strip() if isinstance(source,str) else ''
    return actual and confirmed and bool(evidence) and evidence != '合成演示参数'


def sensitivity(daily, base, stocks, leads, packs):
    from .replay import run_replay
    groups = [list(dict.fromkeys(values)) for values in (stocks, leads, packs)]
    if any(not values for values in groups):
        raise ValueError('每种条件至少选择一项')
    if math.prod(len(values) for values in groups) > 8:
        raise ValueError('本轮最多比较8种组合，请减少选项')
    for values, minimum in zip(groups, (0, 1, 1)):
        if any(isinstance(v, bool) or not isinstance(v, int) or v < minimum for v in values):
            raise ValueError('库存须为非负整数，交期与箱规须为正整数')
    records = []
    for number, (stock, lead, pack) in enumerate(product(*groups), 1):
        config = {**base, 'initial_stock': stock, 'lead_days': lead, 'pack_size': pack}
        _, metrics = run_replay(daily, config)
        records.append(metrics.assign(scenario=number, initial_stock=stock, lead_days=lead, pack_size=pack, evidence_type='模拟回放，不是商户实测'))
    return pd.concat(records, ignore_index=True)


def compare_scenarios(metrics):
    rows = []
    for scenario, group in metrics.groupby('scenario'):
        indexed = group.set_index('policy')
        a, b = indexed.loc['adaptive'], indexed.loc['baseline']
        valid = pd.notna(a.fill_rate) and pd.notna(b.fill_rate) and a.common_eligible_rate == 1
        fill_delta = float(a.fill_rate - b.fill_rate) if valid else None
        inventory_delta = float(a.mean_stock - b.mean_stock)
        conclusion = '零需求或存在人工复核，不能作完整自动策略对比'
        if valid:
            if fill_delta > 1e-9 and inventory_delta > 1e-9:
                conclusion = '满足率提高，但库存占用增加'
            elif fill_delta >= -1e-9 and inventory_delta <= 1e-9:
                conclusion = '本条件下满足率不降、平均库存不增'
            else:
                conclusion = '本条件存在其他取舍，需结合两项指标判断'
        rows.append(dict(scenario=scenario, fill_delta=fill_delta, inventory_delta=inventory_delta, conclusion=conclusion))
    return pd.DataFrame(rows)

APP_REVISION = "audit-fixes-20261104"
