import pandas as pd
import pytest

from core.inventory import parse_inventory


def inventory():
    return pd.DataFrame([dict(sku='001', as_of='2025-04-01', on_hand='8',
        on_order='0', lead_days='3', pack_size='6', min_order='0',
        arrival_confirmed='是', source='盘点记录', parameter_kind='实际盘点', verified='True')])


def test_upload_normalizes_numeric_values_and_resets_verification():
    raw=inventory();raw.loc[0,'lead_days']='3.0'
    result=parse_inventory(raw,raw.columns)
    assert result.loc[0,'lead_days']==3
    assert result.loc[0,'sku']=='001'
    assert bool(result.loc[0,'arrival_confirmed'])
    assert not result.loc[0,'verified']
    assert raw.loc[0,'verified']=='True'


@pytest.mark.parametrize('column,value', [('on_hand','bad'),('on_hand','-1'),
    ('pack_size','0'),('lead_days','1.5'),('on_order','inf'),
    ('arrival_confirmed','maybe'),('as_of','NaT'),('as_of','2025-02-30'),('sku',' ')])
def test_invalid_upload_does_not_mutate_original(column,value):
    raw=inventory();raw.loc[0,column]=value;before=raw.copy(deep=True)
    with pytest.raises(ValueError): parse_inventory(raw,raw.columns)
    pd.testing.assert_frame_equal(raw,before)


def test_empty_duplicate_and_missing_columns_are_rejected():
    raw=inventory()
    for invalid in [raw.iloc[:0],pd.concat([raw,raw]),raw.drop(columns='pack_size')]:
        with pytest.raises(ValueError): parse_inventory(invalid,raw.columns)
