import pandas as pd
import pytest

from core import holdout_study as study


def test_selection_uses_only_earlier_sales_and_keeps_test_zeros(monkeypatch):
    dates = pd.date_range('2025-01-01', '2025-05-31')
    cutoff = pd.Timestamp('2025-05-01')
    daily = pd.concat([
        pd.DataFrame({'date': dates, 'sku': sku, 'sold_qty': early})
        for sku, early in [('a', 10), ('b', 5), ('new', 0)]
    ], ignore_index=True)
    daily.loc[(daily.sku == 'b') & daily.date.gt(cutoff), 'sold_qty'] = 0
    daily.loc[(daily.sku == 'new') & daily.date.gt(cutoff), 'sold_qty'] = 200
    calls = []

    def replay(history, period, policy, setting, value):
        demand = history.loc[history.date.between(*period), 'sold_qty'].sum()
        fill = min(.95, .8 + value)
        calls.append((period[1], history.date.max()))
        return dict(sku=history.sku.iloc[0], policy=policy, parameter=value, demand=demand,
                    fill_rate=fill if demand else None, shortage=(1 - fill) * demand,
                    mean_stock=value * 100, mean_on_order=1, review_rate=0)

    monkeypatch.setattr(study, '_replay_product', replay)
    result = study.run_holdout_study(daily, '2025-05-31', .92)
    assert result['cohort'] == ['a', 'b']
    assert result['active_count'] == result['paired_target_count'] == 1
    assert result['summary'].set_index('policy').parameter.to_dict() == {'baseline': .2, 'adaptive': .25}
    assert result['summary'].set_index('policy').mean_stock_test.to_dict() == {'baseline': 20., 'adaptive': 25.}
    assert len(result['details']) == 4
    assert all(end == max_date for end, max_date in calls)
    assert calls[-5][0] == cutoff and calls[-4][0] > cutoff

    daily.loc[daily.date.gt(cutoff), 'sold_qty'] = 0
    zero = study.run_holdout_study(daily, '2025-05-31', .92)
    assert zero['cohort'] == result['cohort']
    assert zero['summary'].parameter.tolist() == result['summary'].parameter.tolist()
    assert zero['active_count'] == zero['paired_target_count'] == 0


def test_holdout_requires_complete_sales_history():
    daily = pd.DataFrame({'date': pd.date_range('2025-03-01', '2025-05-31'),
                          'sku': 'a', 'sold_qty': 5})
    with pytest.raises(ValueError, match='51 天连续记录'):
        study.run_holdout_study(daily, '2025-05-31')
