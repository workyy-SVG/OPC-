"""Charts and contextual readings for replenishment results."""
from html import escape

import pandas as pd
import plotly.express as px
import streamlit as st

from core.policy import order_quantity
from ui import show_chart


ONBOARDING_REVISION = 1


POLICIES = {'baseline': '固定比例缓冲', 'adaptive': '历史误差缓冲'}
COLORS = {'固定比例缓冲': '#526a84', '历史误差缓冲': '#167969'}
TEAL, SLATE, AMBER = '#167969', '#526a84', '#ad6634'


def _card(title, subtitle, content, foot=''):
    st.markdown(
        f'<div class="visual-card"><h3>{escape(title)}</h3>'
        f'<p class="visual-note">{escape(subtitle)}</p>{content}'
        + (f'<p class="visual-foot">{escape(foot)}</p>' if foot else '')
        + '</div>',
        unsafe_allow_html=True,
    )


def _segment(value, scale, color):
    return f'<span style="width:{value / scale * 100:.4f}%;background:{color}"></span>' if scale else ''


def _rows(items, scale, unit):
    return ''.join(
        f'<div class="visual-row"><div class="visual-row-label"><span><i class="visual-dot" style="background:{color}"></i>'
        f'{escape(label)}</span><strong>{escape(unit(value))}</strong></div>'
        f'<div class="visual-track" aria-hidden="true">{_segment(value, scale, color)}</div></div>'
        for label, value, color in items
    )


def distribution(title, subtitle, items, unit, foot=''):
    total = sum(value for _, value, _ in items)
    bar = ''.join(_segment(value, total, color) for _, value, color in items)
    labels = ''.join(
        f'<div class="visual-tile"><small><i class="visual-dot" style="background:{color}"></i>{escape(label)}</small>'
        f'<strong>{value:,} {escape(unit)}</strong></div>'
        for label, value, color in items
    )
    _card(title, subtitle, f'<div class="visual-track" aria-hidden="true">{bar}</div>'
          f'<div class="visual-summary">{labels}</div>', foot)


def quality_charts(summary, names):
    excluded = sum(value for key, value in summary.items() if key not in ('valid', 'suspicious'))
    valid, suspicious = summary['valid'], summary['suspicious']
    total = valid + excluded
    left, right = st.columns(2)
    with left:
        distribution('销售记录去向',
                     f'共 {total:,} 条记录 · {valid / total:.1%} 计入销量' if total else '本次没有销售记录',
                     [('计入销量', valid, TEAL), ('未计入销量', excluded, SLATE)], '条',
                     f'计入销量的记录中有 {suspicious:,} 条疑似重复，可在下方核对。' if suspicious else '计入销量的记录将用于补货计算。')
    with right:
        items = [
            (names[key], value, AMBER if key in ('suspicious', 'error') else SLATE)
            for key, value in summary.items() if key != 'valid' and value
        ]
        items.sort(key=lambda item: item[1], reverse=True)
        if items:
            _card('需要关注的记录', '橙色标出疑似重复和错误记录',
                  _rows(items, items[0][1], lambda value: f'{value:,} 条'),
                  '下方明细按处理方式分组，可查原记录和修正建议。')
        else:
            _card('需要关注的记录', '本次未发现需要核对或排除的记录。', '')


def inventory_chart(inventory):
    stock = inventory[['sku', 'on_hand', 'on_order']]
    if stock.empty:
        _card('库存分布', '当前没有库存记录。', '')
        return
    if stock[['on_hand', 'on_order']].drop_duplicates().shape[0] == 1:
        row = stock.iloc[0]
        distribution('库存分布', f'{len(stock):,} 个商品使用相同数量',
                     [('每个商品的现货', int(row.on_hand), TEAL), ('每个商品的在途', int(row.on_order), SLATE)], '件',
                     '修改任一商品的现货或在途后，这里会展示商品间的差异。')
        return
    top = stock.assign(total=stock.on_hand + stock.on_order).nlargest(10, 'total').copy()
    top['sku'] = top.sku.astype(str)
    top = top.rename(columns={'on_hand': '现货', 'on_order': '在途'})
    long = top.melt(id_vars='sku', value_vars=['现货', '在途'], var_name='库存类型', value_name='件数')
    fig = px.bar(long[long['件数'] > 0], x='件数', y='sku', color='库存类型', orientation='h',
                 color_discrete_map={'现货': TEAL, '在途': SLATE}, category_orders={'sku': top.sku.tolist()},
                 title='现货与在途 · 库存总量前 10', labels={'sku': '商品编码'})
    fig.update_layout(bargap=.42)
    fig.update_yaxes(type='category', autorange='reversed', title=None, showgrid=False)
    show_chart(fig, '同一行的两段分别是现货和在途。下方明细保留全部商品。', height=340)


def replay_comparison(metrics):
    left, right = st.columns(2)
    indexed = metrics.set_index('policy')
    for column, metric, title, subtitle, formatter in (
        (left, 'fill_rate', '件数满足率', '已满足件数占模拟需求件数的比例', lambda value: f'{value:.1%}'),
        (right, 'mean_stock', '平均期末库存', '每天期末现货的平均件数', lambda value: f'{value:,.1f} 件'),
    ):
        with column:
            values = indexed[metric].dropna()
            if values.empty:
                _card(title, subtitle, '', '测试区间没有需求，满足率不适用。')
                continue
            items = [(name, values[key], COLORS[name]) for key, name in POLICIES.items() if key in values]
            scale = 1 if metric == 'fill_rate' else values.max()
            delta = values.get('adaptive', 0) - values.get('baseline', 0)
            difference = f'{delta * 100:+.1f} 个百分点' if metric == 'fill_rate' else f'{delta:+,.1f} 件'
            _card(title, subtitle, _rows(items, scale, formatter), f'历史误差缓冲相对固定比例缓冲 {difference}')


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
        ('fill_rate', '每组条件的件数满足率', '同组两根柱使用相同初始库存、交期和每箱件数。悬停可看具体条件。无需求时满足率不适用。'),
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
    st.write('示例固定日均销量 5 件、误差缓冲 6 件、最小起订量 15 件。调整输入观察目标、缺口和按整箱取整后的建议量。')
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
