"""Purchase workbench kept separate from the main navigation."""
import json
from pathlib import Path
from uuid import uuid4

import pandas as pd
import plotly.express as px
import streamlit as st

from ui import show_chart
from core.policy import recommend
from core.purchasing import add_purchase, receive, project, save_workspace, load_workspace, receive_into_inventory, effective_stock, register_purchase, workspace_path, check_workspace_source, read_workspace


ONBOARDING_REVISION = 1


def render(daily, inventory, as_of, require_actual=False, product_label=str):
    st.subheader('采购与到货')
    st.caption('先核对建议，再登记人工确认的采购。记录不会向供应商自动下单。')
    st.caption(f'本次库存决策日为 {as_of.date()}，对应销售覆盖结束后的第一天。示例使用历史日期。')
    st.session_state.setdefault('purchase_workspace', uuid4().hex)
    st.session_state.setdefault('purchases', [])
    st.session_state.setdefault('stock_movements', [])
    if st.session_state.pop('purchase_form_reset', False):
        st.session_state.purchase_ack = False
        st.session_state.purchase_reason = ''
        st.session_state.purchase_request_id = uuid4().hex
    st.session_state.setdefault('purchase_request_id', uuid4().hex)
    path = workspace_path(st.session_state.purchase_workspace)
    source_hash = st.session_state.get('sales_signature', st.session_state.data_hash)

    def persist(ledger, updated_inventory=None):
        inv = st.session_state.inventory if updated_inventory is None else updated_inventory
        revision = save_workspace(path, ledger, inv, st.session_state.stock_movements, source_hash=source_hash, sales_updates=st.session_state.get("sales_updates",[]), expected_revision=st.session_state.get('workspace_revision',0))
        st.session_state.workspace_revision = revision
        st.session_state.purchases = ledger
        st.session_state.pipeline_semantics='external-only-v1'
        st.session_state.inventory = inv

    from core.storage import remote_enabled, StorageError
    try:
        remote=remote_enabled()
    except StorageError as error:
        st.error(str(error))
        return
    st.info('当前选择Supabase存储，请先测试连接；成功写入后可用恢复码跨重启恢复。' if remote else
            '当前使用本机存储；请定期下载备份。可在同一实例用恢复码恢复，云端重建后本机文件可能丢失。')
    with st.expander('备份与恢复'):
        if remote and st.button('测试持久存储连接（不消耗AI额度）'):
            try:
                from core.storage import usage
                used,remaining=usage()
                st.success(f'数据库连接成功；今日AI已用{used}次，剩余{remaining}次。')
            except StorageError as error:
                st.error(str(error))
        st.download_button('下载采购记录备份', json.dumps(dict(schema=2, pipeline_semantics='external-only-v1', source_hash=source_hash, sales_updates=st.session_state.get("sales_updates",[]), purchases=st.session_state.purchases, inventory=json.loads(st.session_state.inventory.to_json(orient='records')), movements=st.session_state.stock_movements), ensure_ascii=False, indent=2), 'purchases.json', 'application/json')
        st.caption('工作区恢复码仅限本人保管，拥有此码可恢复对应业务记录。使用本机存储时，仅在磁盘保留时可恢复。')
        st.code(st.session_state.purchase_workspace, language=None)
        recovery = st.text_input('已有工作区恢复码',type='password')
        if st.button('恢复工作区',disabled=not recovery):
            try:
                raw = read_workspace(recovery.strip())
                payload = json.loads(raw)
                check_workspace_source(payload,source_hash)
                ledger,inv,movements = load_workspace(raw)
                if set(inv.sku) != set(inventory.sku): raise ValueError('商品集合不一致')
                st.session_state.purchases=ledger
                st.session_state.inventory=inv
                st.session_state.stock_movements=movements
                st.session_state.sales_updates=payload.get('sales_updates',[])
                st.session_state.pipeline_semantics='external-only-v1'
                st.session_state.purchase_workspace=recovery.strip()
                st.session_state.workspace_revision=payload.get('revision',0)
                st.session_state.purchase_form_reset=True
                st.session_state.pop('ai_purchase_draft',None)
                st.rerun()
            except (ValueError,TypeError,KeyError,OSError):
                st.error('无法恢复：请核对恢复码、销售数据和本机存储是否仍存在。')
        backup = st.file_uploader('恢复采购备份', type=['json'])
        allow_legacy = st.checkbox('兼容旧备份：已核对来源与覆盖范围，外部在途不含台账采购')
        if st.button('应用采购备份', disabled=backup is None):
            try:
                payload=json.loads(backup.getvalue())
                check_workspace_source(payload,source_hash,allow_legacy)
                ledger, restored_inventory, movements = load_workspace(backup.getvalue())
                if set(restored_inventory.sku) != set(inventory.sku):
                    raise ValueError('备份商品与当前库存不一致，请先加载对应销售数据')
                revision = save_workspace(path, ledger, restored_inventory, movements, source_hash=source_hash, sales_updates=payload.get("sales_updates",[]), expected_revision=st.session_state.get('workspace_revision',0))
                st.session_state.workspace_revision = revision
                st.session_state.purchases = ledger
                st.session_state.pipeline_semantics='external-only-v1'
                st.session_state.inventory = restored_inventory
                st.session_state.stock_movements = movements
                st.session_state.sales_updates = payload.get('sales_updates',[])
                st.session_state.purchase_form_reset=True
                st.session_state.pop('ai_purchase_draft',None)
                st.rerun()
                st.success('采购备份已恢复')
            except (ValueError, TypeError, KeyError, OSError) as error:
                st.error('恢复失败：' + str(error))

    from ai_ui import purchasing_ai
    purchasing_ai(inventory, as_of, persist)

    sku = st.selectbox('采购商品', inventory.sku.tolist(), format_func=product_label)
    stock = inventory.set_index('sku').loc[sku].to_dict() | {'sku': sku}
    try:
        from core.inventory import parse_inventory
        parse_inventory(pd.DataFrame([stock]), inventory.columns)
    except (ValueError, TypeError, KeyError):
        st.error('库存配置存在无效数量或日期，请先返回库存配置修正。')
        return
    history = daily[daily.sku == sku]
    recommendation_stock = effective_stock(stock, st.session_state.purchases, as_of)
    st.caption('建议统一使用：外部在途 + 本项目采购剩余在途；逾期或超交期的明细需人工复核。')
    recommendation = recommend(history, recommendation_stock, {'require_actual': require_actual}, as_of)
    suggested = recommendation['qty']
    st.write('当前规则建议：' + (str(suggested) + '件' if suggested is not None else '需人工复核'))
    with st.form('confirm_purchase'):
        qty = st.number_input('确认采购数量（件）', min_value=1, value=max(1, int(suggested or 1)), step=1)
        ordered = st.date_input('采购日期', value=as_of.date())
        expected = st.date_input('预计到货日期', value=(as_of + pd.Timedelta(days=int(stock['lead_days']))).date())
        reason = st.text_input('确认或修改原因', key='purchase_reason', placeholder='例如：每箱件数、预算调整、人工复核依据')
        acknowledged = st.checkbox('我已核对库存、已有采购及供应商条件，确认登记此笔采购', key='purchase_ack')
        external = st.checkbox('这是外部汇总在途的明细化登记（不新增在途总量）')
        if st.form_submit_button('登记确认采购'):
            try:
                if not acknowledged:
                    raise ValueError('请先核对并确认采购条件')
                ledger, inv = register_purchase(st.session_state.purchases, st.session_state.inventory,
                    sku, qty, ordered, expected, reason, st.session_state.purchase_request_id, external, suggested)
                persist(ledger, inv)
                st.session_state.purchase_form_reset = True
                st.session_state.receipt_notice = '采购已保存，已刷新在途和建议；再次采购请重新填写并确认。'
                st.rerun()
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
        ledger_table = pd.DataFrame(table)
        delivery = ledger_table.groupby('预计到货', as_index=False)[['已到货','剩余在途']].sum()
        delivery = delivery.melt(id_vars='预计到货', var_name='收货状态', value_name='件数')
        figure = px.bar(delivery, x='预计到货', y='件数', color='收货状态', title='按采购单预计日期汇总收货进度')
        figure.update_xaxes(type='date')
        show_chart(figure, '每根柱按采购单的预计日期归类。实际收货日期请查下方库存流水。')
        st.dataframe(ledger_table, hide_index=True)
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
                        revision = save_workspace(path, ledger, updated_inventory, movements, source_hash=source_hash, sales_updates=st.session_state.get("sales_updates",[]), expected_revision=st.session_state.get('workspace_revision',0))
                        st.session_state.workspace_revision = revision
                        st.session_state.purchases = ledger
                        st.session_state.pipeline_semantics='external-only-v1'
                        st.session_state.inventory = updated_inventory
                        st.session_state.stock_movements = movements
                        st.session_state['receipt_notice'] = '到货已入账，现货已自动增加，库存流水已保存。相同凭证重复提交不会再次入库。'
                        st.rerun()
                    except (ValueError, TypeError, OSError) as error:
                        st.error(str(error))

    if 'receipt_notice' in st.session_state:
        st.success(st.session_state.pop('receipt_notice'))
    if st.session_state.get('sales_updates'):
        with st.expander('销售更新与库存扣减记录'):
            st.dataframe(pd.DataFrame(st.session_state.sales_updates),hide_index=True)
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
        external_pending = float(stock['on_order']) > 0
        if external_pending:
            st.warning('外部汇总在途缺少到货日期，风险表只包含已登记明细。请使用明细化登记后再判断全部库存风险。')
        shortages = projection[projection.shortage > 0]
        if not shortages.empty:
            st.warning('预计首次缺货：' + shortages.iloc[0]['date'] + '，请核对采购到货安排。')
        elif not external_pending:
            st.success('当前假设下，未来14天未预测到缺货。')
        risk = projection.rename(columns={'date':'日期','arrival':'当日到货','demand':'预计销量',
                                          'ending_stock':'预计期末现货','shortage':'预计未满足需求'})
        figure = px.line(risk, x='日期', y='预计期末现货', markers=True, title=f'{sku} · 未来14天预计现货（件）',
                         hover_data=['当日到货','预计销量','预计未满足需求'])
        if not shortages.empty:
            figure.add_scatter(x=shortages.date, y=shortages.ending_stock, mode='markers',
                               name='预计缺货日', marker=dict(color='#b15a25', size=10),
                               customdata=shortages[['shortage']], hovertemplate='%{x}<br>预计未满足 %{customdata[0]:.1f} 件<extra></extra>')
        figure.update_xaxes(type='date')
        show_chart(figure, '橙色圆点表示预计有需求无法满足的日期。悬停可查看缺口及到货信息。')
        st.dataframe(risk, hide_index=True)
        st.caption('这是固定日均需求下的模拟；未包含波动缓冲、缺货失销量、保质期和资金约束。确认收货自动更新现货；跨日销售和盘点仍需核对。')

APP_REVISION = "audit-fixes-20261104"
