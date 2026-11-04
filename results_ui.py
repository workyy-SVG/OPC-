"""Charts and contextual readings for replenishment results."""
import pandas as pd
import plotly.express as px
import streamlit as st

from core.policy import order_quantity
from ui import show_chart


POLICIES = {'baseline': '固定比例缓冲', 'adaptive': '历史误差缓冲'}
COLORS = {'固定比例缓冲': '#4d7193', '历史误差缓冲': '#217664'}


def quality_charts(summary, names):
    excluded = {names[key]: value for key, value in summary.items() if key not in ('valid', 'suspicious')}
    kept, isolated = st.columns(2)
    with kept:
        data = pd.DataFrame({'处理结果': ['纳入销量', '未纳入销量'], '记录数': [summary['valid'], sum(excluded.values())]})
        show_chart(px.bar(data, x='处理结果', y='记录数', text='记录数', title='销售记录去向',
                          color_discrete_sequence=['#217664']),
                   f"疑似重复的 {summary['suspicious']:,} 条已包含在纳入销量中，需人工核对。", height=290)
    with isolated:
        data = pd.DataFrame(list(excluded.items()), columns=['处理原因', '记录数'])
        fig = px.bar(data, x='记录数', y='处理原因', text='记录数', orientation='h',
                     title='未纳入销量的记录明细', color_discrete_sequence=['#b15a25'])
        fig.update_yaxes(autorange='reversed')
        show_chart(fig, '查看哪些记录按规则排除，以及哪些需要修正。下方问题明细可定位原文件。', height=290)


def replay_reading(metrics):
    a, b = metrics.set_index('policy').loc[['adaptive', 'baseline']].to_dict('records')
    stock_change = a['mean_stock'] - b['mean_stock']
    stock = f"平均期末库存从 {b['mean_stock']:,.1f} 件变为 {a['mean_stock']:,.1f} 件（{stock_change:+,.1f} 件）"
    if pd.isna(a['fill_rate']) or pd.isna(b['fill_rate']):
        st.info(f'**本次结果怎么读**　测试期销量为 0，件数满足率不适用。{stock}。')
    else:
        points = (a['fill_rate'] - b['fill_rate']) * 100
        st.info(f"**本次结果怎么读**　从固定比例切换到历史误差缓冲，件数满足率由 {b['fill_rate']:.1%} "
                f"变为 {a['fill_rate']:.1%}（{points:+.1f} 个百分点），{stock}。"
                f"累计未满足需求由 {b['shortage']:,.0f} 件变为 {a['shortage']:,.0f} 件。")
    if a['common_eligible_rate'] < 1:
        st.caption(f"两种规则都能给出数量的日期占 {a['common_eligible_rate']:.1%}。其余日期至少一方需人工复核，解读差异时要考虑这部分。")


def scenario_charts(scenarios, comparisons):
    data = scenarios.copy()
    data['补货策略'] = data.policy.map(POLICIES)
    data['条件'] = '第 ' + data.scenario.astype(str) + ' 组'
    data['采购条件'] = (data.initial_stock.astype(str) + ' 件初始库存 / ' +
                    data.lead_days.astype(str) + ' 天交期 / 每箱 ' + data.pack_size.astype(str) + ' 件')
    conditions = data.drop_duplicates('scenario').set_index('scenario')['采购条件']
    comparable = comparisons.dropna(subset=['fill_delta'])
    if comparable.empty:
        st.info('**本次结果怎么读**　各组含零需求或人工复核日期，完整的策略差值不适用。可继续核对库存图和下方明细。')
    else:
        highlights = comparable.loc[comparable.fill_delta.abs().idxmax()]
        scenario = int(highlights.scenario)
        st.info(f'**本次结果怎么读**　{len(comparable)} / {len(conditions)} 组可完整比较。'
                f'满足率差绝对值最大的为第 {scenario} 组（{conditions.loc[scenario]}）。'
                f'历史误差缓冲相对固定比例缓冲的满足率差为 {highlights.fill_delta * 100:+.1f} 个百分点，'
                f'平均期末库存差为 {highlights.inventory_delta:+,.1f} 件。')
    for metric, title, caption in (
        ('fill_rate', '每组条件的件数满足率', '同组两根柱使用相同初始库存、交期和箱规。悬停可看具体条件；无需求的组不显示满足率。'),
        ('mean_stock', '每组条件的平均期末库存', '同组内与上图一起读。满足率上升同时库存增加，表示服务表现与备货占用之间有取舍。'),
    ):
        values = data.dropna(subset=[metric])
        if values.empty:
            continue
        fig = px.bar(values, x='条件', y=metric, color='补货策略', barmode='group',
                     color_discrete_map=COLORS, hover_data=['采购条件'],
                     labels={metric: '满足率' if metric == 'fill_rate' else '平均期末库存（件）'}, title=title)
        if metric == 'fill_rate':
            fig.update_yaxes(tickformat='.0%', range=[0, 1])
            fig.update_traces(hovertemplate='%{x}<br>%{customdata[0]}<br>满足率 %{y:.1%}')
        show_chart(fig, caption)


def study_charts(result):
    summary = result['summary'].dropna(subset=['fill_rate']).copy()
    summary['补货策略'] = summary.policy.map(POLICIES)
    summary['对照状态'] = summary.dominated.map({True: '被支配', False: '未被支配'})
    if summary.empty:
        st.info('本次扫描没有可计算的满足率。请检查测试期销量与日期覆盖。')
    else:
        fig = px.scatter(summary, x='mean_stock', y='fill_rate', color='补货策略', symbol='对照状态',
                         color_discrete_map=COLORS, hover_data=['parameter'],
                         labels={'mean_stock':'商品平均库存（件）','fill_rate':'商品平均满足率','parameter':'参数'},
                         title='参数扫描 · 库存占用与满足率')
        fig.update_yaxes(tickformat='.0%')
        fig.update_traces(marker_size=11, hovertemplate='平均库存 %{x:,.1f} 件<br>平均满足率 %{y:.1%}<br>参数 %{customdata[0]}')
        show_chart(fig, '每个点是一组参数，横轴越左表示库存越少，纵轴越高表示满足率越高。'
                   '找横坐标接近的点比较。若另一点库存不更多且满足率不更低，并至少有一项更好，原点标为被支配。')

    equal = result['equal_inventory'].dropna(subset=['difference']).copy()
    if equal.empty:
        st.info('**本次等库存结果**　当前扫描未提供可插值的等库存对照。可在散点图查看两类规则各自覆盖的库存范围。')
    else:
        equal['历史误差系数'] = equal.scale.astype(str)
        equal['满足率差（百分点）'] = equal.difference * 100
        st.info(f"**本次等库存结果**　有 {len(equal)} 个历史误差参数可对照。"
                f"同等平均库存下，满足率差为 {equal.difference.min() * 100:+.1f} 至 {equal.difference.max() * 100:+.1f} 个百分点。")
        fig = px.bar(equal, x='历史误差系数', y='满足率差（百分点）', hover_data=['mean_stock'],
                     labels={'mean_stock':'平均库存（件）'}, title='等库存满足率差')
        fig.add_hline(y=0, line_color='#667c76')
        show_chart(fig, '历史误差缓冲减固定比例插值。正值表示历史误差缓冲在该库存水平下的估计满足率更高。插值限于已有点的库存范围。')

    ci = result['ci']
    if ci['difference'] is None:
        st.write('配对比较缺少可计算的满足率。')
    else:
        st.write(f"固定参数配对比较覆盖 {ci['n']} 个商品，平均满足率差 {ci['difference'] * 100:+.1f} 个百分点。")
        if ci['low'] is None:
            st.caption('至少需要 2 个商品才可估计区间。')
        else:
            st.caption(f"95% 配对自助区间为 {ci['low'] * 100:+.1f} 至 {ci['high'] * 100:+.1f} 个百分点。"
                       '区间包含零时，现有结果不足以确认差异方向。固定参数比较允许库存不同，需结合等库存图阅读。')


def guide_example():
    st.subheader('动手试算 · 补货件数如何得出')
    st.write('示例固定日均销量 5 件、误差缓冲 6 件、最小起订量 15 件。调整输入观察目标、缺口和箱规取整。')
    left, middle, right = st.columns(3)
    lead = left.slider('示例采购交期（天）', 1, 7, 3)
    stock = middle.slider('示例现货与按期在途（件）', 0, 50, 12)
    pack = right.slider('示例每箱件数', 1, 12, 6)
    qty, target = order_quantity(5, 6, stock, lead, 15, pack)
    gap = max(0, target - stock)
    frame = pd.DataFrame({'计算项目':['目标库存','当前库存位置','补货缺口','建议件数'],
                          '件数':[target, stock, gap, qty]})
    show_chart(px.bar(frame, x='计算项目', y='件数', text='件数', title='从目标库存到建议件数'),
               f'保护期为 {lead + 1} 天，目标 {target} 件，缺口 {gap} 件。'
               + (f'满足起订量后按 {pack} 件/箱向上取整，建议 {qty} 件。' if gap else '现有库存已达到目标，建议 0 件。'),
               height=300)
