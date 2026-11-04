"""Purchase workbench kept separate from the main navigation."""
import json
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

from core.policy import recommend
from core.purchasing import add_purchase, receive, project, save_workspace, load_workspace, receive_into_inventory


def render(daily, inventory, as_of, require_actual=False):
    st.subheader('采购与到货')
    st.caption('先核对建议，再登记人工确认的采购。记录不会向供应商自动下单。')
    st.session_state.setdefault('purchase_workspace', uuid4().hex)
    st.session_state.setdefault('purchases', [])
    st.session_state.setdefault('stock_movements', [])
    path = Path('outputs/purchases') / (st.session_state.purchase_workspace + '.json')

    def persist(ledger):
        save_workspace(path, ledger, st.session_state.inventory, st.session_state.stock_movements)
        st.session_state.purchases = ledger

    st.info('记录按当前会话隔离，并保存到本机。重新打开会话时请导入下载的备份；公开部署不能把本机文件当作永久存储。')
    with st.expander('备份与恢复'):
        st.download_button('下载采购记录备份', json.dumps(dict(schema=2, purchases=st.session_state.purchases, inventory=json.loads(st.session_state.inventory.to_json(orient='records')), movements=st.session_state.stock_movements), ensure_ascii=False, indent=2), 'purchases.json', 'application/json')
        backup = st.file_uploader('恢复采购备份', type=['json'])
        if st.button('应用采购备份', disabled=backup is None):
            try:
                ledger, restored_inventory, movements = load_workspace(backup.getvalue())
                if set(restored_inventory.sku) != set(inventory.sku):
                    raise ValueError('备份商品与当前库存不一致，请先加载对应销售数据')
                save_workspace(path, ledger, restored_inventory, movements)
                st.session_state.purchases = ledger
                st.session_state.inventory = restored_inventory
                st.session_state.stock_movements = movements
                st.rerun()
                st.success('采购备份已恢复')
            except (ValueError, TypeError, KeyError, OSError) as error:
                st.error('恢复失败：' + str(error))

    from ai_ui import purchasing_ai
    purchasing_ai(inventory, as_of, persist)

    sku = st.selectbox('采购商品', inventory.sku.tolist())
    stock = inventory.set_index('sku').loc[sku].to_dict() | {'sku': sku}
    history = daily[daily.sku == sku]
    related = [r for r in st.session_state.purchases if r['sku'] == sku]
    recommendation_stock = dict(stock)
    if related:
        pending_orders = [(r, r['qty'] - sum(x['qty'] for x in r['receipts'])) for r in related]
        recommendation_stock['on_order'] = sum(q for r, q in pending_orders)
        recommendation_stock['arrival_confirmed'] = bool(stock['arrival_confirmed']) and all(
            as_of <= pd.Timestamp(r['expected_at']) <= as_of + pd.Timedelta(days=int(stock['lead_days']))
            for r, q in pending_orders if q > 0)
        st.caption('本页建议使用台账剩余在途替代汇总在途；逾期或超出采购交期的未到货订单转人工复核。请确认台账完整。')
    recommendation = recommend(history, recommendation_stock, {'require_actual': require_actual}, as_of)
    suggested = recommendation['qty']
    st.write('当前规则建议：' + (str(suggested) + '件' if suggested is not None else '需人工复核'))
    with st.form('confirm_purchase'):
        qty = st.number_input('确认采购数量（件）', min_value=1, value=max(1, int(suggested or 1)), step=1)
        ordered = st.date_input('采购日期', value=as_of.date())
        expected = st.date_input('预计到货日期', value=(as_of + pd.Timedelta(days=int(stock['lead_days']))).date())
        reason = st.text_input('确认或修改原因', placeholder='例如：供应商箱规、预算调整、人工复核依据')
        acknowledged = st.checkbox('我已核对库存、已有采购及供应商条件，确认登记此笔采购')
        if st.form_submit_button('登记确认采购'):
            try:
                if not acknowledged:
                    raise ValueError('请先核对并确认采购条件')
                persist(add_purchase(st.session_state.purchases, sku, qty, ordered, expected, reason, suggested))
                st.success('已保存采购记录')
            except (ValueError, TypeError, OSError) as error:
                st.error(str(error))

    records = st.session_state.purchases
    if records:
        table = []
        for record in records:
            received = sum(r['qty'] for r in record['receipts'])
            table.append({'采购编号': record['id'], '商品': record['sku'], '采购数量': record['qty'],
                          '已到货': received, '剩余在途': record['qty'] - received,
                          '预计到货': record['expected_at'], '确认或修改原因': record['reason']})
        st.dataframe(pd.DataFrame(table), hide_index=True)
        pending = [r for r in records if sum(x['qty'] for x in r['receipts']) < r['qty']]
        if pending:
            chosen = st.selectbox('登记到货的采购', [r['id'] for r in pending],
                                  format_func=lambda key: next(r['sku'] + ' / ' + r['expected_at'] + ' / ' + key[:8] for r in pending if r['id'] == key))
            with st.form('receive_purchase'):
                quantity = st.number_input('本次到货数量（件）', min_value=1, value=1, step=1)
                date = st.date_input('实际到货日期', value=as_of.date())
                receipt_id = st.text_input('收货凭证编号', help='每批到货使用独立编号；重复提交同一编号不会重复入库。')
                if st.form_submit_button('保存到货记录'):
                    try:
                        ledger, updated_inventory, movements = receive_into_inventory(
                            records, st.session_state.inventory, st.session_state.stock_movements,
                            chosen, quantity, date, receipt_id)
                        save_workspace(path, ledger, updated_inventory, movements)
                        st.session_state.purchases = ledger
                        st.session_state.inventory = updated_inventory
                        st.session_state.stock_movements = movements
                        st.session_state['receipt_notice'] = '到货已入账，现货已自动增加，库存流水已保存。相同凭证重复提交不会再次入库。'
                        st.rerun()
                    except (ValueError, TypeError, OSError) as error:
                        st.error(str(error))

    if 'receipt_notice' in st.session_state:
        st.success(st.session_state.pop('receipt_notice'))
    if st.session_state.stock_movements:
        st.subheader('收货库存流水')
        st.dataframe(pd.DataFrame(st.session_state.stock_movements).rename(columns={
            'receipt_id':'收货凭证','purchase_id':'采购编号','sku':'商品','date':'日期',
            'qty':'入库数量','before':'入库前现货','after':'入库后现货'}), hide_index=True)
    st.subheader('未来14天库存风险')
    st.caption('使用决策日前28天均值预测，逐日先到货再扣需求；缺货不结转。仅统计采购台账中的分批到货，原库存配置的汇总在途不重复加入。')
    snapshot_date = st.date_input('风险预测起始日（当前库存日期）', value=as_of.date())
    st.caption('当前现货已包含确认入库的数量；预测不会重复加入这些到货。')
    opening = st.number_input('预测起始现货（已含确认入库）', min_value=0, value=int(stock['on_hand']), step=1, key='risk_stock_' + sku + '_' + str(stock['on_hand']))
    start = pd.Timestamp(snapshot_date)
    if start != pd.Timestamp(stock['as_of']).normalize():
        st.warning('请使用当前库存日期预测；其他日期须先更新对应日期的库存快照。')
        return
    recent = history.set_index('date').sold_qty.reindex(pd.date_range(start - pd.Timedelta(days=28), periods=28))
    if recent.isna().any():
        st.warning('起始日前28天覆盖不完整，不能生成库存风险预测。')
    else:
        projection, overdue = project(st.session_state.purchases, sku, opening, start, recent.mean(), accounted_receipts={m['receipt_id'] for m in st.session_state.stock_movements})
        if overdue:
            st.warning(f'有{overdue}件采购已逾期未登记到货，未假定今天自动到货，请联系供应商核实。')
        if float(stock['on_order']) > 0:
            st.warning('库存配置还有汇总在途，请核对是否已逐笔登记；未登记部分不会计入此预测。')
        shortages = projection[projection.shortage > 0]
        if not shortages.empty:
            st.warning('预计首次缺货：' + shortages.iloc[0]['date'] + '，请核对采购到货安排。')
        else:
            st.success('当前假设下，未来14天未预测到缺货。')
        st.dataframe(projection.rename(columns={'date':'日期','arrival':'当日到货','demand':'预计销量','ending_stock':'预计期末现货','shortage':'预计未满足需求'}), hide_index=True)
        st.caption('这是固定日均需求下的模拟；未包含波动缓冲、缺货失销量、保质期和资金约束。确认收货自动更新现货；跨日销售和盘点仍需核对。')
