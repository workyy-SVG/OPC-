"""Judge-facing comparison at a shared service target."""
import io
import json
import zipfile

import pandas as pd
import plotly.express as px
import streamlit as st

from core.export import csv_bytes
from core.holdout_study import (
    GRIDS, LEAD_DAYS, MAX_PRODUCTS, MIN_CALIBRATION_UNITS, MIN_SELLING_DAYS,
    PACK_SIZE, PERIOD_DAYS, QUANTILE, WARMUP_DAYS, WINDOW, experiment_periods, run_holdout_study,
)
from core.policy import VERSION
from results_ui import COLORS, POLICIES
from ui import show_chart


def render(daily, product_label):
    st.subheader('同一个满足率目标，需要备多少库存？')
    st.write('先用前一段历史给两种方法分别选参数，再锁定参数，用后一段历史检验。两种方法使用同一批商品、日期和采购条件。')
    confirmed = daily.loc[daily.coverage_status.eq('confirmed'), 'date']
    if confirmed.empty or (confirmed.max() - confirmed.min()).days + 1 < 2 * PERIOD_DAYS + WARMUP_DAYS:
        st.info(f'至少需要 {2 * PERIOD_DAYS + WARMUP_DAYS} 天连续销售记录。可先使用公开样本，或在下方探索区分析较短区间。')
        return

    left, right = st.columns(2)
    minimum_end = confirmed.min() + pd.Timedelta(days=2 * PERIOD_DAYS + WARMUP_DAYS - 1)
    end = left.date_input('留出期最后一天', value=confirmed.max().date(), min_value=minimum_end.date(),
                          max_value=confirmed.max().date(),
                          key='holdout_end', help='从这一天往前取 30 天留作测试，再往前取 30 天用来选参数。')
    target = right.selectbox('共同满足率目标', [.80, .85, .90, .95], index=2,
                             format_func=lambda value: f'{value:.0%}', key='holdout_target',
                             help='满足率是模拟中满足的需求件数除以测试期销量。请在运行前确定目标。')
    calibration, test = experiment_periods(end)
    st.caption(f'选参期 {calibration[0].date()} 至 {calibration[1].date()}　→　留出期 {test[0].date()} 至 {test[1].date()}')
    st.caption(f'按选参期销量取前 {MAX_PRODUCTS} 个商品。每个至少售出 {MIN_CALIBRATION_UNITS} 件且至少 '
               f'{MIN_SELLING_DAYS} 天有销量。历史销量用作需求估计。')
    st.caption(f'两种方法的交期均为 {LEAD_DAYS} 天，每箱 {PACK_SIZE} 件。每段开始时，现货按之前 {WINDOW} 天的日均销量估算，初始在途为零。')
    st.caption(f"两种方法各试 {len(GRIDS['baseline'][1])} 个参数，选达标且平均现货最少的设置。"
               '无候选达标时，保留选参期满足率最高的设置，并标明未达标。')
    if st.button('运行留出期对照', type='primary'):
        with st.spinner('正在用较早的销售记录选参，再检验留出期…'):
            try:
                st.session_state.holdout_result = run_holdout_study(daily, end, target)
            except ValueError as error:
                st.error(str(error))
    result = st.session_state.get('holdout_result')
    if result is None:
        return
    if result['test'][1] != str(end) or result['target'] != target:
        st.info('下方保留上次结果。点「运行留出期对照」应用当前日期和目标。')
    _render_result(result, product_label)


def _render_result(result, product_label):
    summary = result['summary'].set_index('policy')
    details = result['details']
    target = result['target']
    size, active = len(result['cohort']), result['active_count']
    st.subheader('留出期结果')
    st.caption(f"{' 至 '.join(result['test'])} · 目标 {target:.0%} · 锁定 {size} 个商品 · "
               f"{active} 个有销量 · {result['paired_target_count']} 个商品两种方法均达到目标")
    if active:
        columns = st.columns(2)
        for column, (policy, name) in zip(columns, POLICIES.items()):
            row = summary.loc[policy]
            with column, st.container(border=True):
                st.markdown(f'**{name}**')
                st.metric('留出期平均现货', f"{row.mean_stock_test:,.1f} 件")
                status = '达到目标' if row.test_met else '未达到目标'
                st.write(f"满足率 **{row.fill_rate_test:.2%}** · {status}")
                st.caption(f"平均在途 {row.mean_on_order_test:,.1f} 件 · 未满足 {row.shortage_test:,.0f} 件")
        _conclusion(summary, target)
    else:
        st.info('锁定商品在留出期均无销量，满足率不适用。可下载数据查看留出期记录。')
    if active < size:
        st.caption(f'{size - active} 个商品在留出期无销量。逐商品明细保留这些记录，整体满足率和现货统计均使用有销量的同一批商品。')
    st.caption('整体满足率按需求件数加权。平均现货合计各商品每日收市时的库存。结果适用于本次历史模拟。')

    if active:
        chart = details[details.demand.gt(0)].copy()
        chart['商品'] = chart.sku.map(product_label)
        chart['方法'] = chart.policy.map(POLICIES)
        chart['满足率'] = chart.fill_rate.map(lambda value: f'{value:.1%}')
        chart['平均现货（件）'] = chart.mean_stock
        fig = px.bar(chart, x='平均现货（件）', y='商品', color='方法', barmode='group',
                     orientation='h', color_discrete_map=COLORS, hover_data=['满足率'],
                     title='逐商品比较平均现货')
        show_chart(fig, '横条越短表示现货越少。悬停可同时查看满足率，再判断是否存在取舍。', height=315)
    with st.expander('锁定参数与逐商品明细'):
        choices = summary.reset_index().assign(
            policy=lambda data: data.policy.map(POLICIES),
            fill_rate_calibration=lambda data: data.fill_rate_calibration.map(lambda value: f'{value:.1%}'),
            calibration_met=lambda data: data.calibration_met.map({True: '是', False: '否'}),
        )
        st.dataframe(choices[['policy','parameter','fill_rate_calibration','calibration_met']].rename(columns={
            'policy':'方法', 'parameter':'锁定参数', 'fill_rate_calibration':'选参期满足率', 'calibration_met':'选参期达标',
        }), hide_index=True)
        st.caption('固定比例的参数是预测需求的附加比例，例如 0.20 代表多备 20%。历史误差的参数是历史误差缓冲的倍数。')
        display = details.assign(
            sku=details.sku.map(product_label), policy=details.policy.map(POLICIES),
            fill_rate=details.fill_rate.map(lambda value: '不适用' if pd.isna(value) else f'{value:.1%}'),
            meets_target=details.meets_target.map({True: '是', False: '否'}),
        )
        st.dataframe(display[['sku','policy','demand','fill_rate','mean_stock','meets_target']].rename(columns={
            'sku':'商品', 'policy':'方法', 'demand':'测试期销量（件）', 'fill_rate':'满足率',
            'mean_stock':'平均现货（件）', 'meets_target':'留出期达标',
        }), hide_index=True)
    _download(result)


def _conclusion(summary, target):
    if not summary.calibration_met.all():
        st.warning('至少一种方法在选参期未达到目标，已锁定其选参期满足率最高的设置。留出期结果仍可查看，当前实验不足以估计两种方法达到目标所需的最低库存。')
        return
    if not summary.test_met.all():
        st.info(f'留出期至少一种方法未达到 {target:.0%}，此时库存差不能直接解释为在同一目标下的节省。')
        return
    a, b = summary.loc['adaptive'], summary.loc['baseline']
    difference = a.mean_stock_test - b.mean_stock_test
    points = (a.fill_rate_test - b.fill_rate_test) * 100
    direction = '相同' if abs(difference) < .05 else f"{'少' if difference < 0 else '多'} {abs(difference):,.1f} 件"
    st.info(f'两种方法均达到 {target:.0%}。历史误差缓冲的平均现货相对固定比例缓冲{direction}，'
            f'实际满足率差为 {points:+.2f} 个百分点。')


def _download(result):
    meta = {key: result[key] for key in ('cohort', 'calibration', 'test', 'target', 'active_count', 'paired_target_count')}
    meta.update(data_hash=st.session_state.data_hash, version=VERSION, grids=GRIDS,
                calibration_minimum_units=MIN_CALIBRATION_UNITS, calibration_minimum_selling_days=MIN_SELLING_DAYS,
                maximum_products=MAX_PRODUCTS, history_window=WINDOW, lead_days=LEAD_DAYS, warmup_days=WARMUP_DAYS,
                pack_size=PACK_SIZE, quantile=QUANTILE,
                initial_stock='ceil(mean of preceding 28 daily sales × (lead days + 1))',
                selection='minimum average ending stock at target in calibration; fallback highest fill rate, then lowest inventory',
                evidence='Historical simulation. Sales approximate demand. No live store result.')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for key in ('summary', 'details', 'candidates'):
            archive.writestr(key + '.csv', csv_bytes(result[key]))
        archive.writestr('manifest.json', json.dumps(meta, ensure_ascii=False, indent=2))
    st.download_button('下载留出期对照报告', buffer.getvalue(), 'holdout-comparison.zip', 'application/zip')
