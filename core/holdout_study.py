"""Select replenishment settings before evaluating a later holdout period."""
import math

import pandas as pd

from .replay import run_replay


PERIOD_DAYS = 30
MAX_PRODUCTS = 5
MIN_CALIBRATION_UNITS = 30
MIN_SELLING_DAYS = 5
WINDOW = 28
LEAD_DAYS = 3
PACK_SIZE = 1
QUANTILE = .9
WARMUP_DAYS = WINDOW + LEAD_DAYS + 20
GRIDS = {
    'baseline': ('baseline_ratio', (.10, .20, .40, .80, 1.60, 3.20, 6.40, 12.80, 16., 19.20, 25.60)),
    'adaptive': ('adaptive_scale', (.25, .50, .75, 1., 1.50, 2., 3., 4., 6., 8., 12.)),
}


def experiment_periods(test_end):
    end = pd.Timestamp(test_end).normalize()
    test_start = end - pd.Timedelta(days=PERIOD_DAYS - 1)
    calibration_end = test_start - pd.Timedelta(days=1)
    calibration_start = calibration_end - pd.Timedelta(days=PERIOD_DAYS - 1)
    return (calibration_start, calibration_end), (test_start, end)


def _replay_product(history, period, policy, setting, parameter):
    start, end = period
    # Both policies begin with the same stock, using only sales before this phase.
    recent = history[history.date.between(start - pd.Timedelta(days=WINDOW), start - pd.Timedelta(days=1))]
    if len(recent) != WINDOW or recent.sold_qty.isna().any():
        raise ValueError('每个区间开始前都需要至少 28 天连续、已核验的销售记录。')
    initial = math.ceil(recent.sold_qty.mean() * (LEAD_DAYS + 1))
    config = dict(start=str(start.date()), end=str(end.date()), initial_stock=initial, lead_days=LEAD_DAYS,
                  pack_size=PACK_SIZE, window=WINDOW, quantile=QUANTILE)
    timeline, metrics = run_replay(history, config | {setting: parameter}, policies=(policy,))
    return metrics.iloc[0].to_dict() | {
        'sku': history.sku.iloc[0], 'parameter': parameter, 'initial_stock': initial,
        'demand': float(timeline.demand.sum()), 'mean_on_order': float(timeline.on_order.mean()),
    }


def _totals(rows):
    demand = rows.demand.sum()
    return {
        'fill_rate': 1 - rows.shortage.sum() / demand if demand else None,
        'mean_stock': rows.mean_stock.sum(),
        'mean_on_order': rows.mean_on_order.sum(),
        'demand': demand,
        'shortage': rows.shortage.sum(),
    }


def run_holdout_study(daily, test_end, target=.9):
    """Fit each policy against one target on earlier data, then evaluate both once."""
    if not 0 < target <= 1:
        raise ValueError('满足率目标须大于 0% 且不超过 100%。')
    calibration, test = experiment_periods(test_end)
    before_test = daily[daily.date <= calibration[1]]
    selection = before_test[before_test.date.between(*calibration)]
    demand = selection.groupby('sku').sold_qty.agg(units='sum', selling_days=lambda qty: (qty > 0).sum())
    cohort = (demand[(demand.units >= MIN_CALIBRATION_UNITS) & (demand.selling_days >= MIN_SELLING_DAYS)]
              .sort_values('units', ascending=False, kind='stable').head(MAX_PRODUCTS).index.tolist())
    if not cohort:
        raise ValueError(f'选参期没有销量达到 {MIN_CALIBRATION_UNITS} 件且至少 {MIN_SELLING_DAYS} 天有销量的商品。')

    # To compare complete policies, all selected products need a continuous
    # prehistory long enough to produce the first historical-error buffer.
    dates = pd.date_range(calibration[0] - pd.Timedelta(days=WARMUP_DAYS), test[1])
    selected = daily[daily.sku.isin(cohort) & (daily.date <= test[1])]
    coverage = selected.pivot(index='date', columns='sku', values='sold_qty').reindex(dates)
    if coverage.isna().any().any():
        raise ValueError(f'留出期和选参期需完整覆盖，选参开始前还需 {WARMUP_DAYS} 天连续记录。')
    calibration_histories = [before_test[before_test.sku == sku] for sku in cohort]
    test_histories = [selected[selected.sku == sku] for sku in cohort]
    candidates, chosen = [], []
    for policy, (setting, grid) in GRIDS.items():
        scans = []
        for value in grid:
            rows = pd.DataFrame([_replay_product(history, calibration, policy, setting, value)
                                 for history in calibration_histories])
            if rows.review_rate.gt(0).any():
                raise ValueError('选参期有日期无法生成采购建议，请检查选参期前的历史覆盖。')
            scans.append({'policy': policy, 'parameter': value, **_totals(rows)})
        eligible = [row for row in scans if row['fill_rate'] >= target]
        winner = (min(eligible, key=lambda row: (row['mean_stock'], row['parameter'])) if eligible else
                  min(scans, key=lambda row: (-row['fill_rate'], row['mean_stock'], row['parameter'])))
        candidates.extend(scans)
        chosen.append(winner | {'calibration_met': bool(eligible)})
    details = pd.DataFrame([
        _replay_product(history, test, row['policy'], GRIDS[row['policy']][0], row['parameter'])
        for row in chosen for history in test_histories
    ])
    details['meets_target'] = details.fill_rate.notna() & details.fill_rate.ge(target) & details.review_rate.eq(0)
    active = details[details.demand > 0]
    active_count = active.sku.nunique()
    # Keep one schema even when every selected product has zero test demand.
    metrics = ('fill_rate', 'mean_stock', 'mean_on_order', 'demand', 'shortage')
    summary = pd.DataFrame(chosen).rename(columns={key: key + '_calibration' for key in metrics})
    test_rows = []
    for row in chosen:
        rows = active[active.policy == row['policy']]
        totals = _totals(rows)
        if rows.empty:
            # No active cohort: inventory aggregates are not applicable, not zero.
            totals.update(mean_stock=None, mean_on_order=None)
        test_rows.append({'policy': row['policy'],
                          **{key + '_test': value for key, value in totals.items()},
                          'reviewed': bool(rows.review_rate.gt(0).any())})
    summary = summary.merge(pd.DataFrame(test_rows), on='policy')
    summary['test_met'] = summary.fill_rate_test.notna() & summary.fill_rate_test.ge(target) & ~summary.reviewed
    return {
        'summary': summary, 'details': details, 'candidates': pd.DataFrame(candidates),
        'cohort': cohort, 'active_count': active_count,
        'paired_target_count': int(details.groupby('sku').meets_target.all().sum()),
        'calibration': tuple(str(day.date()) for day in calibration),
        'test': tuple(str(day.date()) for day in test), 'target': target,
    }

APP_REVISION = "audit-fixes-20261104"


def run_rolling_holdout(daily, test_end, target=.9, windows=3):
    """Predeclare non-overlapping 30-day holdouts; retain failed windows too."""
    from .numbers import integer
    windows=integer(windows,2,3)
    end=pd.Timestamp(test_end).normalize()
    if pd.isna(end): raise ValueError('留出期结束日期不合法')
    output=[]
    for offset in reversed(range(windows)):
        current_end=end-pd.Timedelta(days=PERIOD_DAYS*offset)
        calibration,test=experiment_periods(current_end)
        item=dict(calibration=tuple(str(d.date()) for d in calibration),test=tuple(str(d.date()) for d in test))
        try:
            item['result']=run_holdout_study(daily[daily.date<=current_end],current_end,target)
            item['error']=None
        except ValueError as error:
            item['result']=None
            item['error']=str(error)
        output.append(item)
    return output
