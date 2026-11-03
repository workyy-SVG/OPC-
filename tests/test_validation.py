import pandas as pd
import pytest
from core.policy import recommend
from core.validation import sensitivity, compare_scenarios, parameter_status


def history():
    return pd.DataFrame(dict(sku=['001']*100,date=pd.date_range('2025-01-01',periods=100),sold_qty=[5]*100))


def test_actual_mode_blocks_demo_and_unconfirmed():
    stock=dict(sku='001',as_of='2025-04-01',on_hand=8,on_order=0,lead_days=3,pack_size=6,min_order=0,arrival_confirmed=True,source='合成演示参数')
    result=recommend(history(),stock,{'require_actual':True},'2025-04-01')
    assert result['qty'] is None and 'UNVERIFIED_PARAMETERS' in result['reason_codes']
    stock.update(parameter_kind='实际盘点',verified=True,source='盘点表-20250401；供应商确认单-01')
    assert parameter_status(stock)
    assert recommend(history(),stock,{'require_actual':True},'2025-04-01')['qty'] is not None
    stock['verified']='True'
    assert not parameter_status(stock)


def test_scenarios_keep_both_policies_and_conditions():
    metrics=sensitivity(history(),dict(start='2025-04-01',end='2025-04-03'),[0,20],[2],[1,6])
    assert len(metrics)==8
    assert metrics.groupby('scenario').size().eq(2).all()
    assert set(metrics.initial_stock)=={0,20}
    assert set(metrics.pack_size)=={1,6}
    assert len(compare_scenarios(metrics))==4


def test_scenario_limits_and_invalid_values():
    for stocks,leads,packs in [([], [2], [1]), ([0,1,2],[2,3],[1,6]), ([-1],[2],[1])]:
        with pytest.raises(ValueError): sensitivity(history(),{},stocks,leads,packs)


def test_zero_demand_has_no_effect_claim():
    data=history();data['sold_qty']=0
    metrics=sensitivity(data,dict(start='2025-04-01',end='2025-04-03'),[0],[2],[1])
    comparison=compare_scenarios(metrics)
    assert pd.isna(comparison.iloc[0].fill_delta)
    assert '不能' in comparison.iloc[0].conclusion
