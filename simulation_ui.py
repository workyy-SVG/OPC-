"""Historical-data demonstration, isolated from operational session stock."""
import pandas as pd
import plotly.express as px
import streamlit as st

from ui import show_chart
from core.simulation import create, step, report, export_json, fingerprint


ONBOARDING_REVISION = 1


LABELS = {'date':'模拟日期','opening_stock':'日初现货（到货前）','arrived':'模拟到货',
          'historical_demand':'历史销量代理','fulfilled':'模拟实际出库','shortage':'模拟未满足需求',
          'order':'自动模拟采购','ending_stock':'模拟期末现货','on_order':'模拟在途','review':'需人工复核',
          'id':'模拟采购编号','ordered_at':'模拟下单日','due':'预计模拟到货日',
          'qty':'件数','status':'状态','received_at':'模拟实际到货日',
          'type':'模拟变动类型','delta':'数量变动','before':'变动前现货','after':'变动后现货'}


def render(daily, product_label=str):
    st.caption('这个演练用历史销量和假设库存逐日计算，结果保存在独立的模拟记录中。')
    st.write('每天依次：模拟到货入库 → 仅依据前日及以前销量生成采购 → 载入当日历史销量并模拟出库。库存不足时记录未满足需求，不出现负库存。')
    skus = sorted(daily.sku.unique().tolist())
    default = skus.index('85123A') if '85123A' in skus else 0
    sku = st.selectbox('模拟商品', skus, index=default, format_func=product_label)
    selected = daily[daily.sku == sku].copy()
    start_default = max(selected.date.min(), selected.date.max() - pd.Timedelta(days=29))
    with st.form('simulation_setup'):
        left, right = st.columns(2)
        start = left.date_input('模拟开始日期', value=start_default.date())
        end = right.date_input('模拟结束日期', value=selected.date.max().date())
        initial = st.number_input('模拟初始库存', min_value=0, value=100, step=1)
        lead = st.number_input('模拟采购交期（天）', min_value=1, max_value=365, value=3, step=1)
        pack = st.number_input('模拟每箱件数', min_value=1, value=6, step=1)
        policy = st.selectbox('模拟补货规则', ['adaptive','baseline'], format_func=lambda p:'历史误差缓冲' if p=='adaptive' else '固定20%缓冲')
        if st.form_submit_button('创建或重置模拟'):
            try:
                prepared = create(selected, start, end, initial, lead, pack, policy)
                st.session_state.simulation = prepared
                st.session_state.simulation_running = False
            except (ValueError, TypeError) as error:
                st.error(str(error))
    state = st.session_state.get('simulation')
    if not state:
        st.info('先创建模拟，再选择逐日推进或自动运行。默认示例使用固定补充案例85123A，不按策略优劣选品。')
        return
    if state['sku'] != sku or state['data_hash'] != fingerprint(selected):
        st.session_state.simulation_running = False
        st.warning('当前商品或数据已变化，请重新创建模拟。旧结果未混入新数据。')
        return
    st.caption(f"本次模拟 {state['sku']} · {state['config']['start']} 至 {state['config']['end']} · 初始库存 {state['config']['initial_stock']} 件 · 交期 {state['config']['lead_days']} 天 · 每箱 {state['config']['pack_size']} 件。修改表单后须重新创建模拟。")
    st.session_state.setdefault('simulation_running', False)
    a,b,c = st.columns(3)
    if a.button('逐日推进', disabled=state['completed'] or st.session_state.simulation_running):
        try:
            st.session_state.simulation = step(state, selected)
            st.rerun()
        except (ValueError, TypeError) as error:
            st.error(str(error))
    if b.button('自动运行', disabled=state['completed'] or st.session_state.simulation_running):
        st.session_state.simulation_running = True
        st.rerun()
    if c.button('暂停模拟', disabled=not st.session_state.simulation_running):
        st.session_state.simulation_running = False
        st.rerun()
    simulation_dashboard(selected)


@st.fragment(run_every='2s')
def simulation_dashboard(daily):
    state = st.session_state.simulation
    if st.session_state.simulation_running and not state['completed']:
        try:
            state = step(state, daily)
            st.session_state.simulation = state
        except (ValueError, TypeError) as error:
            st.session_state.simulation_running = False
            st.error('模拟停止：' + str(error))
    if state['completed']:
        was_running = st.session_state.simulation_running
        st.session_state.simulation_running = False
        if was_running:
            st.rerun()
        st.success('本段历史模拟已完成。期末在途仍保留，未假定提前收货。')
    elif st.session_state.simulation_running:
        st.info('自动运行中：约每2秒推进一个历史日。可暂停；关闭页面不继续运行。')
    else:
        st.info('模拟已暂停；页面刷新不会重复推进同一天。')
    summary = report(state)
    metrics = st.columns(4)
    metrics[0].metric('已模拟天数', summary['days'])
    metrics[1].metric('模拟现货', summary['ending_stock'])
    metrics[2].metric('模拟在途', summary['on_order'])
    metrics[3].metric('模拟件数满足率', '无需求' if summary['fill_rate'] is None else f"{summary['fill_rate']:.1%}")
    st.caption(f"历史销量代理{summary['historical_demand']}件 · 模拟出库{summary['fulfilled']}件 · 未满足{summary['shortage']}件 · 人工复核{summary['review_days']}天。库存、交期和到货均是假设。")
    if state['timeline']:
        last = state['timeline'][-1]
        st.subheader('最近模拟日：' + last['date'])
        st.write(f"到货{last['arrived']}件 → 模拟采购{last['order']}件 → 载入历史销量{last['historical_demand']}件 → 实际模拟出库{last['fulfilled']}件 → 期末库存{last['ending_stock']}件")
        decision = state['decisions'][-1]
        if decision['reason_codes']:
            st.warning('该日资料不足，未自动模拟采购：' + '、'.join(decision['reason_codes']))
        with st.expander('该日采购计算证据'):
            st.json(decision)
        timeline = pd.DataFrame(state['timeline'])
        stock = timeline[['date','ending_stock','on_order']].rename(columns=LABELS)
        stock['模拟日期'] = pd.to_datetime(stock['模拟日期'])
        figure = px.line(stock, x='模拟日期', y=['模拟期末现货','模拟在途'], markers=True,
                         labels={'value':'件数','variable':'库存类型'}, title='模拟库存如何变化')
        show_chart(figure, '期末现货是当天出库后剩余的件数。在途是已模拟下单、尚未入库的件数。')
        demand = timeline[['date','fulfilled','shortage']].rename(columns=LABELS)
        demand['模拟日期'] = pd.to_datetime(demand['模拟日期'])
        show_chart(px.bar(demand, x='模拟日期', y=['模拟实际出库','模拟未满足需求'],
                          labels={'value':'件数','variable':'需求结果'}, title='每日需求满足情况',
                          color_discrete_sequence=['#217664','#b15a25']),
                   '两段合计为当天历史销量代理。橙色部分显示模拟库存无法满足的件数。')
        st.subheader('逐日导入与库存记录')
        st.dataframe(timeline.rename(columns=LABELS), hide_index=True)
        with st.expander('模拟采购和分日到货记录'):
            if state['purchases']:
                st.dataframe(pd.DataFrame(state['purchases']).rename(columns=LABELS), hide_index=True)
            else:
                st.write('尚未产生模拟采购。')
        with st.expander('模拟库存流水'):
            st.dataframe(pd.DataFrame(state['movements']).rename(columns=LABELS), hide_index=True)
    st.download_button('下载本次模拟完整记录', export_json(state), 'historical-simulation.json', 'application/json')

APP_REVISION = "audit-fixes-20261104"
