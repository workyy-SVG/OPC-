"""Apply a complete sales snapshot without discarding operational records."""
import pandas as pd
from .numbers import integer
from .purchasing import day


def update_stock(previous, current, inventory, allow_revision=False):
    if current.empty or 'coverage_status' not in current:
        raise ValueError('更新快照没有可用的销售覆盖')
    confirmed = current[current.coverage_status.eq('confirmed')]
    if confirmed.empty:
        raise ValueError('更新快照没有已确认覆盖日期')
    next_date = confirmed.date.max() + pd.Timedelta(days=1)
    result = inventory.copy(deep=True)
    changes = []
    for idx, row in result.iterrows():
        sku = row['sku']; snapshot = day(row['as_of'])
        if next_date < snapshot:
            raise ValueError('销售快照早于当前库存日期，不能回退账目')
        old = previous[(previous.sku == sku) & previous.coverage_status.eq('confirmed') & (previous.date < snapshot)].set_index('date').sold_qty
        new = current[current.sku == sku].set_index('date')
        # A full snapshot must retain all prior verified sales exactly.
        if not old.empty:
            comparable = new.reindex(old.index)
            if comparable.sold_qty.isna().any() or not comparable.coverage_status.eq('confirmed').all():
                raise ValueError(f'商品{sku}历史销售缺失，更新需包含原历史的完整快照')
            if not comparable.sold_qty.eq(old).all():
                from .validation import parameter_status
                if not allow_revision or not parameter_status(row.to_dict()):
                    raise ValueError(f'商品{sku}历史销售被修订，请先保存已核实的实际盘点，再勾选历史对账确认')
                changes.append(dict(sku=sku,start=str(old.index.min().date()),end=str((snapshot-pd.Timedelta(days=1)).date()),
                    sold=0,before=integer(row['on_hand']),after=integer(row['on_hand']),note='历史修订已按实际盘点对账，未重复扣减盘点前销量'))
        dates = pd.date_range(snapshot, next_date - pd.Timedelta(days=1))
        rows = new.reindex(dates)
        if len(dates) and (rows.sold_qty.isna().any() or not rows.coverage_status.eq('confirmed').all()):
            raise ValueError(f'商品{sku}新增日期未完整覆盖，请先补齐数据')
        qty = integer(rows.sold_qty.sum()) if len(dates) else 0
        hand = integer(row['on_hand'])
        if qty > hand:
            raise ValueError(f'商品{sku}新增销售{qty}件超过现货{hand}件，请先登记到货或核对盘点，不会生成负库存')
        result.loc[idx,'on_hand'] = hand - qty
        result.loc[idx,'as_of'] = str(next_date.date())
        if len(dates):
            result.loc[idx,'verified'] = False
            changes.append(dict(sku=sku,start=str(snapshot.date()),end=str((next_date-pd.Timedelta(days=1)).date()),
                                sold=qty,before=hand,after=hand-qty))
    for sku in sorted(set(current.sku)-set(result.sku)):
        result = pd.concat([result,pd.DataFrame([dict(sku=sku,as_of=str(next_date.date()),on_hand=0,on_order=0,
            lead_days=3,pack_size=1,min_order=0,arrival_confirmed=False,source='新增商品待盘点',parameter_kind='未核实',verified=False)])],ignore_index=True)
    return result, changes

APP_REVISION = "audit-fixes-20261104"
