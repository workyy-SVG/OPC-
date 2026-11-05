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
    assert zero['summary'].columns.tolist() == result['summary'].columns.tolist()
    assert zero['summary'].fill_rate_test.isna().all()
    assert zero['summary'].mean_stock_test.isna().all()
    assert not zero['summary'].test_met.any()
    assert zero['summary'].demand_test.eq(0).all()


def test_holdout_requires_complete_sales_history():
    daily = pd.DataFrame({'date': pd.date_range('2025-03-01', '2025-05-31'),
                          'sku': 'a', 'sold_qty': 5})
    with pytest.raises(ValueError, match='51 天连续记录'):
        study.run_holdout_study(daily, '2025-05-31')


@pytest.mark.parametrize('test_demand,legacy', [(0,False),(5,False),(0,True)])
def test_holdout_result_page_and_report_for_zero_and_positive_demand(test_demand,legacy):
    import io
    import json
    import zipfile
    from streamlit.testing.v1 import AppTest
    script = '''
import pandas as pd
import streamlit as st
from core import holdout_study as study
from holdout_ui import _render_result
study.GRIDS = {'baseline':('baseline_ratio',(.2,)), 'adaptive':('adaptive_scale',(1.,))}
daily=pd.DataFrame({'date':pd.date_range('2025-01-01','2025-05-31'),'sku':'a','sold_qty':5})
daily.loc[daily.date.gt('2025-05-01'),'sold_qty']=TEST_DEMAND
result=study.run_holdout_study(daily,'2025-05-31',.9)
if LEGACY:
    fields=('fill_rate','mean_stock','mean_on_order','demand','shortage')
    result['summary']=result['summary'][['policy','parameter','calibration_met']+[f+'_calibration' for f in fields]].rename(columns={f+'_calibration':f for f in fields})
st.session_state.data_hash='zero-demand-test'
st.session_state.result=result
original_download=st.download_button
def capture(label,data,*args,**kwargs):
    st.session_state.exported_zip=data
    return original_download(label,data,*args,**kwargs)
st.download_button=capture
try:
    _render_result(result,lambda sku:sku)
finally:
    st.download_button=original_download
'''.replace('TEST_DEMAND',str(test_demand)).replace('LEGACY',str(legacy))
    # Restore grid overrides after each script so other tests keep their own grids.
    original_grids = study.GRIDS
    try:
        app=AppTest.from_string(script,default_timeout=60).run()
    finally:
        study.GRIDS = original_grids
    assert not app.exception
    result=app.session_state.result
    if not test_demand:
        assert any('满足率不适用' in item.value for item in app.info)
        assert not any('两种方法均达到' in item.value for item in app.info)
        assert not any(m.label=='留出期平均现货' for m in app.metric)
    else:
        assert result['active_count']==1
        assert sum(m.label=='留出期平均现货' for m in app.metric)==2
    with zipfile.ZipFile(io.BytesIO(app.session_state.exported_zip)) as archive:
        assert set(archive.namelist())=={'summary.csv','details.csv','candidates.csv','manifest.json'}
        details=pd.read_csv(io.BytesIO(archive.read('details.csv')))
        assert len(details)==2
        if not test_demand:
            assert details.fill_rate.isna().all()
            assert not details.meets_target.any()
        meta=json.loads(archive.read('manifest.json'))
        assert meta['active_count']==int(test_demand>0)
        assert meta['data_hash']=='zero-demand-test'


def test_rolling_windows_are_fixed_and_failures_are_retained(monkeypatch):
    calls=[]
    def run(daily,end,target):
        calls.append((pd.Timestamp(end),daily.date.max()))
        if len(calls)==1:raise ValueError('历史不足')
        return {'test':str(end)}
    monkeypatch.setattr(study,'run_holdout_study',run)
    daily=pd.DataFrame({'date':pd.date_range('2025-01-01','2025-05-31')})
    result=study.run_rolling_holdout(daily,'2025-05-31',.9,3)
    assert len(result)==3 and result[0]['error']=='历史不足'
    assert result[0]['result'] is None and result[-1]['result'] is not None
    assert all(end==max_date for end,max_date in calls)
    assert (calls[1][0]-calls[0][0]).days==30
    assert (calls[2][0]-calls[1][0]).days==30


def test_cached_holdout_reuses_only_identical_data_and_settings(monkeypatch):
    import holdout_ui
    calls=[]
    def run(daily,end,target):
        calls.append(1)
        return {'total':int(daily.sold_qty.sum()),'target':target}
    monkeypatch.setattr(holdout_ui,'run_holdout_study',run)
    holdout_ui.cached_holdout.clear()
    daily=pd.DataFrame({'sold_qty':[1]})
    first=holdout_ui.cached_holdout(daily,'2025-05-31',.9,'test-version')
    assert holdout_ui.cached_holdout(daily,'2025-05-31',.9,'test-version')==first
    assert len(calls)==1
    holdout_ui.cached_holdout(daily.assign(sold_qty=2),'2025-05-31',.9,'test-version')
    holdout_ui.cached_holdout(daily,'2025-05-31',.95,'test-version')
    assert len(calls)==3
    holdout_ui.cached_holdout.clear()
