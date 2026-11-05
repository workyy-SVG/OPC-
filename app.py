from pathlib import Path
from uuid import uuid4
import hashlib,json,io,zipfile
from importlib import reload, import_module
# Refresh cached modules in dependency order after a hot deployment.
# Current modules carry this release marker, so ordinary reruns and test mocks
# are preserved; only older cached implementations are reloaded.
for _module_name in ['core.numbers', 'core.storage', 'core.purchasing', 'core.inventory', 'core.ingest', 'core.policy', 'core.replay', 'core.validation', 'core.sales_update', 'core.study', 'core.holdout_study', 'core.simulation', 'core.ai_quota', 'core.ai', 'ui', 'results_ui', 'page_help', 'ai_ui', 'purchasing_ui', 'simulation_ui', 'holdout_ui']:
    _module = import_module(_module_name)
    _expected_revision = 'purchase-submit-20261105' if _module_name in ('core.storage', 'core.ai_quota', 'purchasing_ui') else 'audit-fixes-20261104'
    if getattr(_module,'APP_REVISION',None) != _expected_revision:
        reload(_module)
import ai_ui
import page_help
import purchasing_ui
import results_ui
import simulation_ui
import ui

# Streamlit can rerun this entry point with modules cached from the previous deploy.
if not hasattr(results_ui, 'replay_comparison'):
    reload(ui)
    reload(results_ui)
for module in (ai_ui, page_help, purchasing_ui, results_ui, simulation_ui):
    if not getattr(module, 'ONBOARDING_REVISION', 0):
        reload(module)

from ui import table_view, date_range, apply_theme, show_chart
from page_help import PAGE_HELP, render_page_help
from results_ui import (
    AMBER, COLORS, SLATE, TEAL, distribution, guide_example, inventory_chart,
    quality_charts, replay_comparison, replay_reading, scenario_charts, study_charts,
)
import pandas as pd
import streamlit as st
import plotly.express as px
from core.ingest import parse_orders
from core.demand import build_daily
from core.policy import recommend, VERSION
from core.purchasing import effective_stock, workspace_path
from core.inventory import parse_inventory
from core.replay import run_replay
from core.export import csv_bytes
from core.validation import parameter_status, sensitivity, compare_scenarios
st.set_page_config(page_title='小店补货助手',page_icon='📦',layout='wide')
st.markdown('<style>.stApp{background:#f6f8fb}h1{color:#173d38}div[data-testid="stMetric"]{background:white;padding:20px;border-radius:14px}</style>',unsafe_allow_html=True)
st.markdown("""<style>
.block-container{max-width:1280px;padding-top:2.2rem;padding-bottom:4rem}
[data-testid="stSidebar"]{background:#edf3f1;border-right:1px solid #dce6e1}
h1{font-weight:800;letter-spacing:-.03em}h2,h3{color:#234a42}
[data-testid="stMetric"]{border:1px solid #e3eae6;box-shadow:0 3px 12px #173d3805}
[data-testid="stExpander"]{background:#fff;border-radius:12px}
[data-testid="stDataFrame"]{border:1px solid #e1e8e5;border-radius:10px}
.stButton button[kind="primary"]{background:#217664;border-color:#217664;border-radius:8px}
</style>""",unsafe_allow_html=True)
apply_theme()
st.markdown("""<style>
[data-testid="stSidebar"] [data-testid="stVerticalBlock"]:has(> .st-key-ai_quota_panel){min-height:calc(100dvh - 5rem)}
.st-key-ai_quota_panel{margin-top:auto;padding-top:24px}
.st-key-ai_quota_panel h3{color:#edf8f8!important}
.st-key-ai_quota_panel [data-testid="stMetric"]{background:#1d454d!important;border:1px solid #6b8580!important;border-left:4px solid #ffd75e!important;border-radius:14px;padding:18px 20px!important}
.st-key-ai_quota_panel [data-testid="stMetricLabel"],
.st-key-ai_quota_panel [data-testid="stMetricLabel"] *{color:#ffe599!important}
.st-key-ai_quota_panel [data-testid="stMetricValue"],
.st-key-ai_quota_panel [data-testid="stMetricValue"] *{color:#ffda63!important;font-weight:750}
.st-key-ai_quota_panel [data-testid="stCaptionContainer"],
.st-key-ai_quota_panel [data-testid="stCaptionContainer"] *{color:#ffe599!important}
.st-key-ai_quota_panel [role="progressbar"]{background:#557078!important}
.st-key-ai_quota_panel [role="progressbar"]>div{background:#ffda63!important}
</style>""",unsafe_allow_html=True)
st.sidebar.title('📦 小店补货助手')
st.sidebar.caption('让每一件补货，都有据可查')
@st.fragment(run_every='30s')
def ai_budget_sidebar():
    from core.ai_quota import display_usage
    st.subheader('AI每日共享额度')
    try:
        used, remaining = display_usage()
        st.metric('今日剩余调用', f'{remaining} / 100', help='千问和 DeepSeek 共用每日 100 次额度。')
        st.caption(f'AI 助手今日已用 {used} 次 · 全站共享')
        st.progress(min(used / 100, 1.0))
        st.caption('北京时间零点重置。请求失败也计数。')
    except Exception:
        st.warning('额度存储不可用，AI调用暂不可用。')
def navigate(target): st.session_state['navigation']=target
def open_dictionary():
    st.session_state.guide_default_tab = '字段字典'
    navigate('使用指南')

def page_name(name):
    return {'策略回放':'历史模拟对比', '公平实验室':'公平对比实验'}.get(name, name)

def cancel_actual_mode():
    st.session_state.update(usage_mode='演示探索', actual_mode_confirmed=False)

page=st.sidebar.radio('经营导航',['经营总览','导入与质检','库存配置','补货清单','采购与到货','策略回放','公平实验室','模拟经营','数据与说明','使用指南'],key='navigation',format_func=page_name)
if page!='模拟经营': st.session_state.simulation_running=False
st.sidebar.caption('适用于单家门店。采购量按整件计算，建议每天核对库存和到货。')
mode_choice = st.sidebar.radio(
    '库存使用模式', ['演示探索', '实际采购准备'], key='usage_mode',
    help='演示探索可使用假设库存。实际采购准备只为已核对来源的实际盘点库存给出数量。',
    on_change=lambda: st.session_state.update(actual_mode_confirmed=False),
)
if mode_choice == '实际采购准备' and not st.session_state.get('actual_mode_confirmed'):
    with st.sidebar.container(border=True):
        st.markdown('**确认切换到实际采购准备**')
        st.write('演示库存和未核实的商品会转为人工复核，建议数量暂留空。可在库存配置中填写实际盘点来源并确认。')
        st.button('确认切换', on_click=lambda: st.session_state.update(actual_mode_confirmed=True), type='primary')
        st.button('继续演示', on_click=cancel_actual_mode)
usage_mode = '实际采购准备' if mode_choice == '实际采购准备' and st.session_state.get('actual_mode_confirmed') else '演示探索'
st.caption('当前模式 · ' + usage_mode)
st.title(page_name(page))
HEADINGS={
'模拟经营':('让历史交易逐日驱动经营演示','自动导入历史销量、模拟补货与到货，逐日查看库存流转。','历史模拟'),
'采购与到货':('把补货建议落到采购与到货','记录人工确认、分批到货与每日库存风险。','采购执行'),
'公平实验室':('用留出期验证补货方法','两种方法先用较早数据选参，再用同一个满足率目标比较后续库存占用。','实验验证'),
'经营总览':('从销售记录到补货建议','先了解数据与库存准备情况，再处理补货任务。','经营概览'),
'导入与质检':('先把销售记录整理清楚','导入、匹配、核验，确保每一条建议都有可靠输入。','数据准备'),
'库存配置':('把真实库存和采购条件填准确','逐商品核对现货、在途与交期，为补货决策做好准备。','库存管理'),
'补货清单':('先处理异常，再确认采购','检查人工复核事项，逐条核对建议依据，导出待确认清单。','采购决策'),
'策略回放':('在同样的条件下，看清策略取舍','同时观察满足率与库存占用，保留可追溯的实验记录。','经营分析'),
'使用指南':('第一次使用，也能按步骤完成','从五分钟演示开始，随时查阅字段解释和操作帮助。','帮助中心'),
'数据与说明':('知道数据从哪里来，结果适用于哪里','了解样本来源、计算规则与当前产品边界。','数据说明')}
headline,subtitle,tag=HEADINGS[page]
st.markdown(f'<div class="workspace-banner"><span class="banner-tag">小店补货助手 / {tag}</span><h2>{headline}</h2><p>{subtitle}</p><div class="banner-flow">数据准备　／　库存管理　／　采购决策　／　经营分析</div></div>',unsafe_allow_html=True)

render_page_help(page)

with st.sidebar:
    st.divider()
    st.subheader('当前进度')
    st.write('✅ 已完成数据核验' if 'daily' in st.session_state else '① 等待导入销售数据')
    st.write('✅ 已建立库存配置' if 'inventory' in st.session_state else '② 等待填写库存')
    st.caption('各功能页顶部都有用途和衔接说明。使用指南提供路线图和互动算例。')
    with st.container(key='ai_quota_panel'):
        ai_budget_sidebar()
LABELS={'order_id':'订单编号','sku':'商品编码','order_at':'销售日期','qty':'数量（件）','line_id':'订单行编号','status':'处理状态','on_hand':'现货数量（件）','on_order':'外部在途（不含台账采购）','as_of':'库存快照日期','lead_days':'采购提前期（天）','pack_size':'每箱件数','min_order':'最小起订量（件）','arrival_confirmed':'确认在途按期到货','source':'参数来源','row_no':'原文件行号','reason_code':'问题类型','hint':'修正建议','policy':'补货策略','date':'日期','demand':'需求代理（件）','arrived':'当日到货（件）','order':'当日下单（件）','ending_stock':'期末现货（件）','shortage':'未满足需求（件）','review':'是否人工复核','fill_rate':'件数满足率','mean_stock':'平均期末库存（件）','stockout_rate':'缺货天数占比','review_rate':'人工复核率','orders':'下单次数','final_pipeline':'期末在途（件）','common_eligible_rate':'双方可自动建议日期占比','inventory_position':'库存位置（件）','forecast_lead':'交期内预测需求（件）','buffer':'误差缓冲（件）','target':'目标库存（件）','mean':'近28日日均销量','protection_days':'保护期（天）','window_start':'历史窗口起始日','window_end':'历史窗口结束日','sample_count':'有效历史天数','error_samples':'误差窗口数量','rule_version':'规则版本','run_id':'运行编号','reason_codes':'复核原因'}
REASONS={'INVALID_FIELD':'必填内容缺失、日期格式不匹配或数量不是整数','CONFLICTING_LINE_ID':'同一订单行编号对应不同内容，请核对原订单','DUPLICATE_LINE':'重复订单行，已剔除','CANCELLED':'取消订单，不计入销量','RETURN':'退货单独统计，不冲减需求','NOT_COMPLETED':'未完成或零数量订单，不计入销量','NON_PRODUCT':'非商品记录，已排除','SUSPECT_DUPLICATE':'疑似重复但缺少行编号，保留并请人工核对','HISTORY_GAP':'最近28天覆盖不完整，请补齐销售记录并确认覆盖','INSUFFICIENT_ERROR_WINDOWS':'完整误差窗口不足20个，请增加更早的销售历史','ARRIVAL_UNCONFIRMED':'在途到货时间尚未确认，请在库存配置中核对','SNAPSHOT_DATE_MISMATCH':'库存日期与决策日期不一致，请更新库存快照','MISSING_STOCK':'缺少该商品库存，请导入库存配置'}
def reason_text(code):
    if code=='UNVERIFIED_PARAMETERS': return '尚未确认实际盘点参数：请填写真实来源、选择实际盘点并逐项核实。'
    return REASONS.get(code, '请检查'+LABELS.get(code.removeprefix('INVALID_').lower(),code)+'是否为有效非负整数')

LABELS.update({'parameter_kind':'参数性质','verified':'用户已核实声明','evidence_type':'结果证据类型','scenario':'条件编号','initial_stock':'初始库存（件）'})
def chinese(df):
    result=df.copy()
    if 'policy' in result: result['policy']=result.policy.replace({'baseline':'固定比例缓冲','adaptive':'历史误差缓冲'})
    if 'reason_code' in result: result['reason_code']=result.reason_code.map(reason_text)
    if 'status' in result: result['status']=result.status.replace({'completed':'已完成','complete':'已完成','cancelled':'已取消','canceled':'已取消'})
    for col in ['arrival_confirmed','review','verified']:
        if col in result: result[col]=result[col].map(lambda v:'是' if v is True or str(v)=='True' else '否' if v is False or str(v)=='False' else v)
    if 'reason_codes' in result: result['reason_codes']=result.reason_codes.map(lambda codes:'；'.join(reason_text(c) for c in codes) if isinstance(codes,list) else codes)
    for col in ['fill_rate','stockout_rate','review_rate','common_eligible_rate']:
        if col in result: result[col]=result[col].map(lambda v: '不适用' if pd.isna(v) else f'{v:.1%}')
    return result.rename(columns=LABELS|{'unit_price':'单价','description':'商品描述'})
def section(title,description):
    st.subheader(title);st.write(description)

def download(label,df,name): st.download_button(label,csv_bytes(df),name,'text/csv')
DEMO_RANGE = (pd.Timestamp('2010-01-01').date(), pd.Timestamp('2010-06-30').date())
DEMO_SOURCE = '英国零售公开样本'

def save_sales(raw, clean, issues, summary, daily, coverage, is_demo, source, preserve=False, allow_revision=False):
    data_hash = hashlib.sha256(raw).hexdigest()
    signature = hashlib.sha256(daily.sort_values(['sku','date']).to_csv(index=False).encode()).hexdigest()
    previous = st.session_state.get('daily')
    changed = previous is None or st.session_state.get('data_hash') != data_hash or not previous.equals(daily)
    if changed and preserve and previous is not None:
        if st.session_state.get('is_demo') != is_demo:
            raise ValueError('示例与真实数据不能混合更新，请选择新建工作区')
        if 'inventory' in st.session_state:
            from core.sales_update import update_stock
            from core.purchasing import save_workspace
            inventory, updates = update_stock(previous, daily, st.session_state.inventory, allow_revision)
            history = st.session_state.get('sales_updates', []) + updates
            st.session_state.setdefault('purchase_workspace',uuid4().hex)
            if 'purchase_workspace' in st.session_state:
                path = workspace_path(st.session_state.purchase_workspace)
                revision = save_workspace(path, st.session_state.get('purchases',[]), inventory,
                    st.session_state.get('stock_movements',[]), source_hash=signature, sales_updates=history, expected_revision=st.session_state.get('workspace_revision',0))
                st.session_state.workspace_revision = revision
            st.session_state.inventory = inventory
            st.session_state.sales_updates = history
    if changed:
        transient = ['replay_result','scenario_result','study','holdout_result','rolling_result','simulation','ai_purchase_draft','ai_explanation']
        if not preserve:
            transient += ['inventory','purchases','purchase_workspace','stock_movements','receipt_notice','sales_updates',
                          'purchase_request_id','purchase_ack','purchase_reason','workspace_revision','pipeline_semantics']
        for key in transient:
            st.session_state.pop(key,None)
    names = {sku: f'样本商品 {i:02d}' for i, sku in enumerate(sorted(daily.sku.unique()), 1)} if is_demo else {}
    if 'description' in clean:
        named = clean[clean.description.astype(str).str.strip().ne('')].drop_duplicates('sku')
        names.update(named.set_index('sku').description.to_dict())
    st.session_state.update(raw=raw, daily=daily, issues=issues, summary=summary, data_hash=data_hash, sales_signature=signature,
                            product_names=names, sales_source=source, coverage=coverage, is_demo=is_demo)

def apply_inventory(candidate):
    from core.purchasing import save_workspace
    normalized = parse_inventory(candidate, candidate.columns)
    normalized['verified'] = candidate['verified'].map(lambda value: pd.api.types.is_bool(value) and bool(value))
    st.session_state.setdefault('purchase_workspace',uuid4().hex)
    if 'purchase_workspace' in st.session_state:
        revision = save_workspace(workspace_path(st.session_state.purchase_workspace),st.session_state.get('purchases',[]),
            normalized,st.session_state.get('stock_movements',[]),source_hash=st.session_state.get('sales_signature'),
            sales_updates=st.session_state.get('sales_updates',[]),expected_revision=st.session_state.get('workspace_revision',0))
        st.session_state.workspace_revision = revision
    st.session_state.inventory = normalized


def quick_demo(target='补货清单'):
    raw = Path('data/demo/orders.csv').read_bytes()
    clean, issues, summary = parse_orders(pd.read_csv(io.BytesIO(raw), dtype=str), source="uci")
    daily = build_daily(clean, pd.date_range(*DEMO_RANGE))
    save_sales(raw, clean, issues, summary, daily, DEMO_RANGE, True, DEMO_SOURCE)
    cancel_actual_mode()
    navigate(target)

def require_sales():
    if 'daily' in st.session_state and not st.session_state.daily.empty:
        return
    has_data = 'daily' in st.session_state
    st.info('核验结果没有可用商品，请检查问题行或重新导入。' if has_data else
            '这个功能需要已核验的销售记录。可导入自己的 CSV，或加载公开样本体验。')
    left, right = st.columns(2)
    left.button('前往导入与质检', on_click=navigate, args=('导入与质检',), type='primary')
    if not has_data:
        right.button('加载示例数据', on_click=quick_demo, args=(page,))
    st.stop()

def product_label(sku, names=st.session_state.get('product_names', {})):
    name = names.get(sku)
    return f'{name} · {sku}' if name else str(sku)

if page=='经营总览':
    if 'daily' not in st.session_state:
        with st.container(border=True):
            st.subheader('第一次使用？从这里开始')
            first, second = st.columns(2)
            first.button('用示例立即体验', type='primary', on_click=quick_demo, help='加载公开交易和假设库存，直接进入补货清单。')
            second.button('导入自己的销售记录', on_click=navigate, args=('导入与质检',))
            st.caption('销售数据 → 核对库存 → 查看建议 → 登记采购或对比策略')
    ready='daily' in st.session_state and not st.session_state.daily.empty
    cols=st.columns(4)
    cols[0].metric('管理商品',st.session_state.daily.sku.nunique() if ready else '—',help='来自当前已核验数据')
    cols[1].metric('有效销售记录',st.session_state.get('summary',{}).get('valid','—'))
    cols[2].metric('待核对记录',len(st.session_state.issues) if 'issues' in st.session_state else '—')
    cols[3].metric('库存配置商品',len(st.session_state.inventory) if 'inventory' in st.session_state else '—')
    st.markdown('''<style>
    .st-key-home_workspace{margin-top:12px}
    .st-key-home_tasks,.st-key-home_context{background:#fff;border:1px solid #dce5ea;border-radius:18px;padding:24px;box-shadow:0 4px 18px #203d4c06}
    .st-key-home_workspace [data-testid="stVerticalBlock"]{gap:12px}
    .st-key-home_workspace .home-section-title{font-size:1.15rem;font-weight:750;color:#213d49;margin:0 0 6px;padding:0;line-height:1.4}
    .home-section-note{font-size:.86rem;color:#647680;margin:0;line-height:1.6}
    .home-task{display:flex;align-items:flex-start;gap:14px;padding:4px 0}
    .home-step{flex-shrink:0;display:flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:10px;background:#edf2f5;color:#657b88;font-size:.8rem;font-weight:750}
    .home-task-title{display:flex;align-items:center;flex-wrap:wrap;gap:8px;color:#213d49;font-size:.95rem;font-weight:650;line-height:1.5}
    .home-task p{font-size:.82rem;color:#627580;margin:5px 0 0;line-height:1.6}
    .home-status{font-size:.7rem;font-weight:500;padding:2px 8px;border-radius:20px;background:#f0f3f5;color:#627580;white-space:nowrap}
    .home-status.current{background:#e2f2ec;color:#176550}
    .home-status.done{background:#eaf0fb;color:#426395}
    .home-task.current .home-step{background:#217664;color:white}
    [class*="st-key-home_task_row_"]{padding:12px 0;border-bottom:1px solid #edf1f4}
    .st-key-home_task_row_04{border-bottom:0}
    .st-key-home_workspace .stButton button{min-height:40px;font-size:.82rem;border-radius:9px}
    .home-next{padding:16px 18px;background:#edf7f3;border:1px solid #d9ebe3;border-radius:12px}
    .home-eyebrow{font-size:.75rem;letter-spacing:.06em;color:#548274;margin-bottom:6px}
    .home-next strong{font-size:1.12rem;color:#1b5a49;line-height:1.5}
    .home-next p{font-size:.82rem;color:#55736a;line-height:1.6;margin:6px 0 0}
    .home-context-label{font-size:.85rem;font-weight:650;color:#294653;margin:0 0 6px}
    .st-key-home_context hr{margin:6px 0;border-color:#edf1f4}
    @media(max-width:900px){.st-key-home_workspace>[data-testid="stLayoutWrapper"]>.stHorizontalBlock{flex-direction:column}.st-key-home_workspace>[data-testid="stLayoutWrapper"]>.stHorizontalBlock>[data-testid="stColumn"]{width:100%!important;flex:1 1 100%!important}}
    @media(max-width:640px){.st-key-home_tasks,.st-key-home_context{padding:18px}.home-task{gap:10px}}
    </style>''',unsafe_allow_html=True)
    next_target='导入与质检' if not ready else '库存配置' if 'inventory' not in st.session_state else '补货清单'
    tasks=[('01','整理销售数据','导入并核验完整日期内的销售记录。','导入与质检',ready),('02','核对库存参数','填写真实现货、在途及供应商交期。','库存配置','inventory' in st.session_state),('03','审核补货建议','先解决人工复核，再检查下单数量。','补货清单',False),('04','比较历史策略','查看缺货与库存之间的取舍。','策略回放','replay_result' in st.session_state)]
    with st.container(key='home_workspace'):
        left,right=st.columns([1.8,1],gap='medium')
        with left, st.container(key='home_tasks'):
            st.markdown('<h3 class="home-section-title">我的工作任务</h3><p class="home-section-note">按步骤完成准备，再查看建议与策略对比。</p>',unsafe_allow_html=True)
            for number,title,body,target,done in tasks:
                available=target=='导入与质检' or ready
                state='current' if target==next_target else 'done' if done else ''
                status='当前步骤' if target==next_target else '已有数据' if done else '待处理' if available else '待导入'
                with st.container(key='home_task_row_'+number):
                    detail,action=st.columns([3,1.25],gap='small',vertical_alignment='center')
                    with detail:
                        st.markdown(f'<div class="home-task {state}"><span class="home-step">{number}</span><div><div class="home-task-title">{title}<span class="home-status {state}">{status}</span></div><p>{body}</p></div></div>',unsafe_allow_html=True)
                    with action:
                        action_label={'导入与质检':'导入数据','库存配置':'核对库存','补货清单':'查看建议','策略回放':'查看对比'}[target]
                        st.button(action_label,key='home_'+target,on_click=navigate,args=(target,),disabled=not available,width='stretch',help='打开'+page_name(target))
            st.caption('统计仅基于当前会话数据。已有库存配置不代表完成真实盘点。')
        with right, st.container(key='home_context'):
            st.markdown('<h3 class="home-section-title">建议下一步</h3>',unsafe_allow_html=True)
            next_body=next(body for _,_,body,target,_ in tasks if target==next_target)
            st.markdown(f'<div class="home-next"><div class="home-eyebrow">从这里继续</div><strong>{page_name(next_target)}</strong><p>{next_body}</p></div>',unsafe_allow_html=True)
            st.button('继续处理 →',type='primary',on_click=navigate,args=(next_target,),width='stretch')
            st.divider()
            st.markdown('<div class="home-context-label">数据范围</div>',unsafe_allow_html=True)
            if ready:
                st.markdown('**'+str(st.session_state.daily.date.min().date())+' — '+str(st.session_state.daily.date.max().date())+'**')
                st.caption('当前数据指纹：'+st.session_state.data_hash[:12])
            else:
                st.caption('尚未导入销售数据，可先使用内置 UCI 样本体验。')
            st.divider()
            st.button('查看五分钟使用指南',on_click=navigate,args=('使用指南',),width='stretch')
    if ready:
        sales = st.session_state.daily.groupby('date', as_index=False).sold_qty.sum(min_count=1)
        sales = sales.rename(columns={'date':'日期','sold_qty':'销量（件）'})
        show_chart(px.line(sales, x='日期', y='销量（件）', title='已确认区间的每日销量'),
                   '合计所有商品的已完成销售。已确认且无销售的日期记为 0，未覆盖日期在图上留空。', height=300)
elif page=='导入与质检':
    ready = 'summary' in st.session_state
    if ready:
        total = sum(value for key, value in st.session_state.summary.items() if key != 'suspicious')
        coverage = st.session_state.get('coverage')
        span = f' · 覆盖 {coverage[0]} 至 {coverage[1]}' if coverage else ''
        st.success(f"当前已核验 · {st.session_state.get('sales_source', '本会话销售数据')} · {total:,} 条{span}")
    if st.session_state.pop('sales_import_finished', False):
        st.session_state.open_sales_import = False
    if not ready or st.toggle('重新导入', key='open_sales_import'):
        if ready:
            import_mode = st.radio('本次销售导入方式', ['更新当前店铺（完整快照，保留采购库存）','新建工作区（替换全部业务记录）'], key='sales_import_mode')
            st.caption('更新模式仅扣减库存日期之后的新销售；重复快照不会重复扣减。历史修订须先对账。新建工作区前请下载备份。')
            allow_revision = st.checkbox('我已完成实际盘点核实，允许对账修订盘点日前的销售（不重复扣库存）')
        section('第一步 · 选择销售数据','上传销售 CSV，或用加州大学欧文分校（UCI）公开的英国零售交易样本。')
        with st.expander('我的表格需要包含哪些字段？'):
            st.markdown('**必填**　订单编号、商品编码、销售日期、销售数量。**选填**　商品名称、订单行编号、单价和订单状态。')
        upload = st.file_uploader('上传销售 CSV', type='csv', help='保留订单号、商品编码的前导零。')
        demo = st.button('一键加载 UCI 真实交易样本', type='primary')
        candidate = st.session_state.get('sales_candidate')
        if demo:
            candidate = (Path('data/demo/orders.csv').read_bytes(), DEMO_SOURCE, True)
        elif upload:
            uploaded = (upload.getvalue(), upload.name, False)
            if st.session_state.get('last_sales_upload_id') != upload.file_id:
                candidate = uploaded
                st.session_state.last_sales_upload_id = upload.file_id
        if candidate != st.session_state.get('sales_candidate'):
            for key in ('map_order_id','map_sku','map_order_at','map_qty','sales_coverage_confirmed',
                        '已完整覆盖的营业日期区间start','已完整覆盖的营业日期区间end'):
                st.session_state.pop(key, None)
            st.session_state.sales_candidate = candidate
        st.caption('样本来源 · UCI Online Retail II / 陈大庆 / CC BY 4.0。默认库存为演示假设。')
        if candidate is not None:
            raw, source, is_demo = candidate
            st.caption('待核验 · ' + source)
            try:
                df = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
            except (ValueError, UnicodeError) as error:
                st.error(f'无法读取 CSV。请检查文件编码和格式。{error}')
            else:
                section('第二步 · 检查字段与日期','核对预览，再对应四个必填列。商品名称可选，填写后会随商品编码显示。')
                table_view(chinese(df.head(10)), hide_index=True)
                from ai_ui import headers_ui
                headers_ui(list(df.columns))
                mapping = {}
                columns = list(df.columns)
                for field in ('order_id','sku','order_at','qty'):
                    selected = st.selectbox('对应字段 · ' + LABELS[field], columns,
                                            index=columns.index(field) if field in columns else 0, key='map_' + field,
                                            format_func=lambda col: LABELS.get(col, col))
                    mapping[selected] = field
                remaining = [col for col in columns if col not in mapping]
                preferred = next((col for col in ('description', '商品名称', '商品描述') if col in remaining), None)
                name_column = st.selectbox('商品名称列（选填）', [None, *remaining],
                                           index=remaining.index(preferred) + 1 if preferred else 0,
                                           format_func=lambda col: '暂不填写' if col is None else col)
                optional_mapping = {}
                used_optional = {name_column} if name_column else set()
                for field, title, aliases in [('status','订单状态列（选填）',('status','订单状态','处理状态','交易状态')),
                                               ('line_id','订单行编号列（选填）',('line_id','订单行编号','行编号'))]:
                    options = [column for column in remaining if column not in used_optional]
                    preferred_optional = next((column for column in aliases if column in options),None)
                    column = st.selectbox(title,[None,*options],index=options.index(preferred_optional)+1 if preferred_optional else 0,
                        format_func=lambda value:'暂不填写' if value is None else value)
                    if column:
                        optional_mapping[column]=field
                        used_optional.add(column)
                formats = {'%Y-%m-%d':'年-月-日，例如 2025-01-31', '%d/%m/%Y':'日/月/年，例如 31/01/2025',
                           '%m/%d/%Y':'月/日/年，例如 01/31/2025', '%Y-%m-%d %H:%M:%S':'年-月-日 时:分:秒，例如 2025-01-31 09:30:00'}
                fmt = st.selectbox('日期格式', list(formats), format_func=formats.get)
                date_col = next((col for col, field in mapping.items() if field == 'order_at'), None)
                dates = pd.to_datetime(df[date_col], format=fmt, errors='coerce').dropna() if date_col else pd.Series(dtype='datetime64[ns]')
                today = pd.Timestamp.today().date()
                defaults = DEMO_RANGE if is_demo else (dates.min().date(), dates.max().date()) if not dates.empty else (today, today)
                section('第三步 · 确认销售记录覆盖日期','只选已经完整导出的日期。所选范围内，没记录的日期按零销量处理。')
                coverage = date_range('已完整覆盖的营业日期区间', value=defaults)
                valid_dates = pd.to_datetime(df[date_col],format=fmt,errors='coerce') if date_col else pd.Series(dtype='datetime64[ns]')
                outside_count = int((valid_dates.notna() & ~valid_dates.between(pd.Timestamp(coverage[0]),pd.Timestamp(coverage[1]))).sum())
                allow_outside = False
                if outside_count:
                    st.warning(f'{outside_count}条记录在所选日期区间外，不会进入该区间销量计算。')
                    allow_outside = st.checkbox('我确认排除区间外记录，仅使用选定覆盖日期',key='allow_outside_'+hashlib.sha256(raw).hexdigest()[:16])
                confirmed = st.checkbox('这些日期都已完整导出', key='sales_coverage_confirmed',
                                        help='如果有一天漏导出，请补齐文件或缩短范围，以免被算成零销量。')
                if st.button('核验并使用此数据', disabled=not confirmed):
                    try:
                        if len(mapping) != 4:
                            raise ValueError('请为四个必填字段选择不同的列。')
                        source_rows = df
                        if name_column != 'description' and 'description' in df and 'description' not in mapping:
                            source_rows = df.drop(columns='description')
                        if name_column is not None:
                            mapping[name_column] = 'description'
                        mapping.update(optional_mapping)
                        if source_rows.rename(columns=mapping).columns.duplicated().any():
                            raise ValueError('所选字段与文件中的标准列名冲突，请调整列名或字段匹配。')
                        clean, issues, summary = parse_orders(source_rows, mapping, fmt, source="uci" if is_demo else "generic")
                        daily = build_daily(clean, pd.date_range(*coverage))
                        if daily.empty and ready:
                            raise ValueError('新文件没有可用的销售记录，已保留当前数据。')
                        outside = clean[~clean.order_at.between(pd.Timestamp(coverage[0]),pd.Timestamp(coverage[1]))]
                        if len(outside) and not allow_outside:
                            raise ValueError(f'有{len(outside)}条有效销售在覆盖区间外，请调整日期或明确允许排除')
                        save_sales(raw, clean, issues, summary, daily, coverage, is_demo, source,
                                   preserve=ready and import_mode.startswith('更新'), allow_revision=ready and allow_revision)
                        st.session_state.sales_import_finished = True
                        st.rerun()
                    except (ValueError, TypeError, KeyError) as error:
                        st.error(str(error))
    if 'summary' in st.session_state:
        section('第四步 · 查看核验结果','已自动处理的记录无需修改；疑似重复仍计入销量，请复核。错误行已隔离，修正后重新导入才能计入。')
        names={'valid':'有效销售','cancelled':'取消订单','returned':'退货记录','suspicious':'疑似重复（已保留）','error':'错误记录','excluded':'非商品记录','duplicate':'已剔除重复'}
        quality_charts(st.session_state.summary, names)
        if st.session_state.issues.empty: st.success('未发现问题记录。下一步：在左侧打开「库存配置」，核对实际现货和交期。')
        else:
            issues=st.session_state.issues
            groups=[('必须修正', ['INVALID_FIELD','CONFLICTING_LINE_ID']),('需要复核',['SUSPECT_DUPLICATE']),('已自动处理',['CANCELLED','RETURN','NOT_COMPLETED','NON_PRODUCT','DUPLICATE_LINE'])]
            for title,codes in groups:
                subset=issues[issues.reason_code.isin(codes)]
                with st.expander(f'{title} · {len(subset)} 条',expanded=title=='必须修正' and len(subset)>0):
                    if len(subset): table_view(chinese(subset),hide_index=True)
            st.info('可继续配置库存；疑似重复可能影响建议数量，实际采购前请完成复核。')
        download('下载问题行',chinese(st.session_state.issues),'issues.csv')
        st.button('下一步：核对库存 →',on_click=navigate,args=('库存配置',))
elif page=='使用指南':
    st.success('建议先用内置样本体验一遍，再替换为自己的订单和库存。整个体验约需 5 分钟。')
    tabs=st.tabs(['快速上手','字段字典','如何理解结果','常见问题','全部功能与 AI'],
                 default=st.session_state.pop('guide_default_tab', '快速上手'))
    with tabs[0]:
        st.subheader('从销售数据到采购记录')
        route = [('导入与质检', '核验销售记录'), ('库存配置', '提供库存和交期'),
                 ('补货清单', '解释建议件数'), ('采购与到货', '登记采购和入库')]
        for index, (target, outcome) in enumerate(route, 1):
            with st.container(border=True):
                label, description, action = st.columns([1.35, 1.2, 1])
                label.markdown(f'**{index:02d}　{target}**')
                description.write(outcome)
                action.button('打开页面', key='guide_route_' + target, on_click=navigate, args=(target,))
        st.caption('收货更新现货后，补货清单会按当前库存重新计算。')
        st.markdown('**想看规则怎样运作？**　模拟经营展示单条规则的逐日变化 → 历史模拟对比查看两条规则的取舍 → 公平对比实验检验相近库存和不同成本假设。')
        with st.expander('按示例完成第一次体验'):
            for title,body in [
                ('1 · 导入与质检','点击「一键加载 UCI 真实交易样本」。保持四项字段映射、日期格式及日期区间默认值，勾选完整覆盖确认，点击「核验并使用此数据」。查看有效销售与问题记录。'),
                ('2 · 库存配置','左侧进入库存配置。选择商品后填写中文表单：现货填当前可卖数量，在途填已下单未到货数量，交期填下单到到货的天数。默认数字都是演示值，真实使用时必须替换。'),
                ('3 · 补货清单','查看三类结果：建议下单、暂不补货、人工复核。展开某商品的计算依据，检查目标库存、现货和在途，然后下载待人工确认的清单。'),
                ('4 · 历史模拟对比','选择一个商品，保持默认测试日期，选择交期并填写初始库存。点击运行，比较两种策略的满足率与库存占用。下载实验 ZIP 可保留逐日记录和配置。')]:
                with st.container(border=True): section(title,body)
            st.info('演示验证：先记住某商品建议数量，再到库存配置增加现货，返回清单观察数量变化。取消「确认在途按期到货」，该商品会转为人工复核。')
    with tabs[1]:
        table_view(pd.DataFrame([
            ['现货数量','当前仓库中可以销售的商品数量。','8 件'],['在途数量','已经采购、尚未入库且预计按期到达的数量。','4 件'],['采购提前期','下单到开门前到货之间的天数，至少为1。','3 天'],['每箱件数','采购数量需要满足的整数倍。','6 件/箱'],['最小起订量','供应商要求的最低采购数量；没有则填0。','15 件'],['库存快照日期','库存盘点对应的决策日期，必须与页面提示一致。','演示为2010-07-01'],['保护期','采购提前期加上1天复核间隔。','交期3天 → 保护期4天'],['库存位置','现货与按期在途之和。','8 + 4 = 12 件']
        ],columns=['字段','填写或计算方法','示例']))
    with tabs[2]:
        guide_example()
        st.warning('人工复核的数量为空，不等于建议0件。请先补齐相关信息再生成建议。')
        st.markdown('**件数满足率：**模拟满足的需求件数占比，越高代表缺货越少。\n\n**平均期末库存：**每天营业结束后的平均存货，越高通常意味着占用更多库存。应与满足率一起比较。\n\n**固定比例缓冲：**在预测保护期需求上增加20%。**历史误差缓冲：**依据历史预测误差确定缓冲，两者共享相同回放条件。')
    with tabs[3]:
        for q,a in [('为什么全部需要人工复核？','检查库存日期和交期，确认在途可按期到货。最近28天必须完整，还需至少20个已经结束的历史误差窗口；建议准备90天以上连续数据。'),('为什么上传后还没有建议？','上传只是读取文件，还需确认字段和日期区间，勾选覆盖确认并点击核验。然后进入库存配置。'),('能直接用Excel文件吗？','当前页面接收CSV。请在Excel中另存为CSV UTF-8，并将订单号、商品编码保留为文本。'),('关闭或刷新页面数据会保存吗？','数据主要保存在当前会话中。请及时下载库存模板、清单和实验结果；新会话需要重新导入。'),('会自动向供应商下单吗？','不会。导出的清单须由经营者审核后自行采购。'),('回放结果是否代表真实利润改善？','不代表。需求来自历史成交量，库存和缺货是模拟值；本工具尚未使用真实商户数据验证。')]:
            with st.expander(q): st.write(a)
    with tabs[4]:
        for name, (purpose, next_use) in PAGE_HELP.items():
            with st.expander(page_name(name)):
                st.write(purpose)
                st.write(next_use)
        st.subheader('AI 辅助功能')
        st.markdown("""
- **导入与质检 · 智能匹配表头**　先按常见列名匹配。启用 AI 后，可发送陌生列名辅助匹配，最终请核对必填字段。
- **补货清单 · AI 补货解释与问答**　选商品并授权发送计算证据及问题。建议数量由补货规则计算，以页面证据为准。
- **采购与到货 · AI 录采购**　从文字或单据照片生成可编辑草稿。逐行核对商品、总件数和日期，再确认登记。收货请另行登记。
- **共享额度**　侧栏显示千问和 DeepSeek 当日合计剩余次数，北京时间零点重置。
""")
elif page=='公平实验室':
    from core import study
    require_sales()
    from holdout_ui import render
    if not getattr(study, 'SAME_COHORT_AXES', False):
        reload(study)
    daily = st.session_state.daily
    comparison_tab, exploration_tab = st.tabs(['留出期对照', '探索参数与成本'])
    with comparison_tab:
        render(daily, product_label)
    with exploration_tab:
        st.caption('这部分用同一段历史扫描参数，适合找取舍和试算成本。留出期对照另行锁定参数检验。')
        daily=st.session_state.daily
        skus = list(daily.sku.unique())
        selected=st.multiselect('参与实验的商品', skus, default=skus[:3], placeholder='请选择商品', format_func=product_label)
        covered = daily.loc[daily.coverage_status.eq('confirmed'), 'date']
        dates=date_range('实验测试日期',(max(covered.min(), covered.max()-pd.Timedelta(days=13)).date(),covered.max().date()))
        c1,c2,c3=st.columns(3)
        lead=c1.number_input('交期（天）',1,30,3,help='从下单到到货的天数，默认用 3 天演示。')
        window=c2.selectbox('参考过去多少天销量',[14,28,56],index=1,help='默认 28 天，覆盖四个完整星期。')
        quantile=c3.selectbox('预测误差取值水平',[.8,.9,.95],index=1,format_func=lambda value:f'{value:.0%}',
                              help='默认 90%，用历史偏差中偏大的值留余量。比例越高，通常留得越多。')
        holding=st.number_input('每件每天持有成本（元，假设值）',min_value=.01,value=1.,help='一件货多存一天的费用，例如仓储和资金占用。')
        penalty=st.number_input('每件缺货成本（元，假设值）',min_value=0.,value=10.,help='少卖一件的估算损失。默认 10 元用于演示。')
        ordering=st.number_input('每次下单成本（元，假设值）',min_value=0.,value=0.,help='每次采购额外产生的固定费用，例如运费。暂不计入可填 0。')
        st.caption('两种方法都从 20 件现货开始，起初没有在途，每箱 1 件。')
        with st.expander('实验术语与适用范围'):
            st.write('**被支配**　有另一组参数用同样多或更少的库存，满足率同样高或更高，且至少一项更好。')
            st.write('**等库存插值**　若两点库存水平不同，用相邻结果估算同等库存下的满足率。估算限于已测试的范围。')
            st.write('**成本敏感性**　调高或调低缺货损失与库存费用的比例，看哪种方法的模拟成本更低。')
            st.write('**事后参照**　看完这段销量才挑出的设置，适合用于比较这次结果。实际采购仍需独立验证。')
        if st.button('运行公平对照实验',type='primary',disabled=not selected):
            cfg=dict(start=str(dates[0]),end=str(dates[1]),initial_stock=20,lead_days=lead,window=window,quantile=quantile)
            with st.spinner('正在逐商品扫描策略参数，请稍候…'):
                try:
                    result=study.run_study(daily[daily.sku.isin(selected)],cfg,holding=holding,shortage_cost=penalty,order_cost=ordering)
                    st.session_state.study=(result,dict(config=cfg,skus=selected,holding=holding,shortage_cost=penalty,order_cost=ordering,data_hash=st.session_state.data_hash,version=VERSION,bootstrap_seed=42,bootstrap_repeats=2000,axis_cohort='positive_demand'))
                except ValueError as e: st.error(str(e))
        if 'study' in st.session_state and st.session_state.study[1].get('axis_cohort') == 'positive_demand':
            result,meta=st.session_state.study
            saved = meta['config']
            st.subheader('本次实验结果与图表说明')
            st.caption(f"已运行 {len(meta['skus'])} 个商品 / {saved['start']} 至 {saved['end']} / 交期 {saved['lead_days']} 天 / 历史窗口 {saved['window']} 天。修改输入后需重新运行。")
            st.write('先看参数散点，再看是否有可比较的等库存结果。两条坐标轴均使用该段有销量的商品，成本统计覆盖全部已选商品。')
            study_charts(result)
            table_view(result['equal_inventory'].rename(columns={'scale':'误差系数','mean_stock':'平均库存','adaptive_fill':'历史误差满足率','baseline_interpolated':'固定比例等库存插值','difference':'满足率差（历史误差减固定比例）'}))
            ci=result['ci']
            st.subheader('看完这段历史后选出的参考设置')
            st.caption('按本次模拟成本给每个商品挑选固定缓冲比例，用来对照结果。')
            table_view(chinese(result['oracle'][['sku','parameter','fill_rate','mean_stock','cost']]).rename(columns={'parameter':'事后选择比例','cost':'假设总成本'}))
            st.subheader('改变成本假设后，哪种方法花费更少？')
            st.write('总成本 = 库存件天 × 每件每日持有成本 + 缺货件数 × 缺货成本 + 下单次数 × 每次下单成本。未计入售价、利润、采购成本及期末在途估值。')
            scan=result['cost_scan'];winners=scan.loc[scan.groupby('shortage_holding_ratio').cost.idxmin()]
            cost_lines = scan.loc[scan.groupby(['shortage_holding_ratio','policy']).cost.idxmin()].copy()
            cost_lines = chinese(cost_lines)
            costs = scan.groupby(['shortage_holding_ratio','policy']).cost.min().unstack()
            delta = costs.adaptive - costs.baseline
            tied = delta.abs() < 1e-8
            st.info(f'**本次成本结果**　扫描的 {len(delta)} 种成本比中，历史误差缓冲最低成本更低的有 '
                    f'{int(((delta < 0) & ~tied).sum())} 种，固定比例缓冲更低的有 {int(((delta > 0) & ~tied).sum())} 种，'
                    f'两者相同的有 {int(tied.sum())} 种。')
            show_chart(px.line(cost_lines.sort_values('shortage_holding_ratio'), x='shortage_holding_ratio', y='cost',
                               color='补货策略', markers=True, color_discrete_map=COLORS, hover_data=['parameter'],
                               labels={'shortage_holding_ratio':'缺货成本 / 持有成本','cost':'模拟总成本（元）','parameter':'参数'},
                               title='两类规则在各成本比下的最低扫描成本'),
                       '在同一个成本比下，越低表示模拟总成本越少。每个点取该规则的最低扫描成本，相邻点可能来自不同参数。')
            table_view(winners.replace({'baseline':'固定比例','adaptive':'历史误差'}).rename(columns={'shortage_holding_ratio':'缺货/持有成本比','policy':'扫描内最低成本策略','parameter':'参数','cost':'总成本'}))
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w') as z:
                for name,df in result.items():
                    if isinstance(df,pd.DataFrame): z.writestr(name+'.csv',csv_bytes(df))
                z.writestr('manifest.json',json.dumps(meta|{'ci':ci},ensure_ascii=False,indent=2))
            st.download_button('下载完整公平实验记录',buf.getvalue(),'公平实验.zip')
elif page=='数据与说明':
    st.markdown(Path('DATA_SOURCES.md').read_text(encoding='utf-8'))
    manifest=Path('data/demo/manifest.json').read_bytes()
    st.download_button('下载样本来源与处理记录',manifest,'数据来源与处理记录.json','application/json')
    st.write('助手用最近 28 天的平均销量估算交期内需求，再根据以往少算的情况留出安全余量。历史销量只记录卖出的件数，缺货时原本还能卖多少无法从订单得知。')
    with st.expander('技术附录 · 计算规则与适用范围'):
        st.write(f'规则版本 {VERSION}。保护期为交期加一天。误差缓冲取已结束误差窗口的最近秩法 90% 分位数，至少 20 个窗口，最多使用最近 60 个。')
        st.write('模拟将历史销量视为当天需求，当天未满足的需求不结转。真实经营效果仍需商户验证。')
else:
    require_sales()
    daily=st.session_state.daily
    as_of=daily[daily.coverage_status=='confirmed'].date.max()+pd.Timedelta(days=1)
    if 'inventory' not in st.session_state and page!='模拟经营':
        st.session_state.inventory=pd.DataFrame([dict(sku=s,as_of=str(as_of.date()),on_hand=10,on_order=0,lead_days=3,pack_size=6,min_order=0,arrival_confirmed=True,source='合成演示参数',parameter_kind='演示假设',verified=False) for s in daily.sku.unique()])
    if st.session_state.get('purchases') and st.session_state.get('pipeline_semantics')!='external-only-v1' and page in ('库存配置','采购与到货','补货清单'):
        st.warning('当前会话含升级前采购记录。新版库存中的外部在途不能重复包含台账采购，请先逐商品核对，再确认新口径。')
        confirmed_semantics=st.checkbox('我已核对所有商品的外部在途，不包含本项目采购台账数量')
        if st.button('确认升级在途口径',disabled=not confirmed_semantics):
            st.session_state.pipeline_semantics='external-only-v1'
            st.rerun()
        if page!='库存配置':
            st.button('先去库存配置核对',on_click=navigate,args=('库存配置',))
            st.stop()
    if page=='模拟经营':
        simulation_ui.render(daily, product_label=product_label)
    elif page=='采购与到货':
        purchasing_ui.render(daily, st.session_state.inventory, as_of, usage_mode=='实际采购准备', product_label=product_label)
    elif page=='库存配置':
        st.caption(f'库存快照日期应为 {as_of.date()}（销售覆盖结束后的第一天）。'
                   + ('当前使用 2010 年的公开样本。' if st.session_state.get('is_demo') else ''))
        section('填写商品库存与采购约束','选择商品，修改下方字段并保存。保存后进入补货清单即可重新计算。商品编码不能编辑；库存快照日期请使用上方提示日期。')
        if usage_mode == '实际采购准备':
            st.info('填写现货和在途，将参数性质设为「实际盘点」，填入来源，勾选核实并保存。')
        else:
            st.button('查看当前库存的补货建议 →', on_click=navigate, args=('补货清单',), type='primary',
                      help='打开补货清单，使用当前已保存的库存。')
        with st.expander('填写说明与批量导入步骤',expanded=False):
            st.markdown('**现货/在途：**均为非负整数；在途只填写预计按期到达的采购。**交期：**至少1天。**每箱件数：**至少1件。**起订量：**没有限制时填0。\n\n批量修改：下载库存模板 → 保留表头和商品编码 → 修改数量和日期 → 上传 → 点击应用。表格中的到货确认填写“是”或“否”。')
        template=st.session_state.inventory
        download('下载库存模板',chinese(template),'inventory.csv')
        f=st.file_uploader('批量导入库存 CSV',type='csv')
        if f and st.button('应用库存文件'):
            try:
                inv=pd.read_csv(f,dtype=str,keep_default_na=False).rename(columns=({'在途数量（件）':'on_order'} | {v:k for k,v in LABELS.items()}))
                inv=parse_inventory(inv,template.columns)
                apply_inventory(inv)
                st.info('导入参数均已重置为未确认，请逐商品核对来源后保存。')
            except (ValueError, KeyError, OSError) as e: st.error(str(e))
        with st.expander('批量设置交期与每箱件数'):
            with st.form('bulk_stock'):
                selected_skus=st.multiselect('选择同一供应商或采购条件的商品',st.session_state.inventory.sku.tolist(),format_func=product_label)
                bulk_lead=st.number_input('统一交期（天）',1,365,3)
                bulk_pack=st.number_input('统一每箱件数',1,10000,6)
                st.caption('只应用于所选商品；变更后需重新核实采购参数。')
                if st.form_submit_button('应用到所选商品'):
                    try:
                        candidate=st.session_state.inventory.copy(deep=True)
                        mask=candidate.sku.isin(selected_skus)
                        candidate.loc[mask,'lead_days']=bulk_lead
                        candidate.loc[mask,'pack_size']=bulk_pack
                        candidate.loc[mask,'verified']=False
                        apply_inventory(candidate)
                        st.success(f'已更新 {int(mask.sum())} 个商品，请重新核实参数。')
                    except (ValueError,TypeError,OSError) as error:
                        st.error('未保存：'+str(error))
        chosen=st.selectbox('选择要修改的商品',st.session_state.inventory.sku.tolist(),format_func=product_label)
        idx=st.session_state.inventory.index[st.session_state.inventory.sku==chosen][0]
        row=st.session_state.inventory.loc[idx].to_dict()
        with st.form('stock_'+chosen):
            st.subheader('商品 '+chosen+' · 库存与采购设置')
            changes={}; columns=st.columns(2)
            for i,k in enumerate(['on_hand','on_order','lead_days','pack_size','min_order']):
                changes[k]=columns[i%2].number_input(LABELS[k],min_value=1 if k in ['lead_days','pack_size'] else 0,value=int(row[k]),step=1)
            changes['as_of']=st.text_input('库存快照日期（年-月-日）',str(row['as_of']))
            changes['arrival_confirmed']=st.checkbox('我确认在途商品可按期到货',value=bool(row['arrival_confirmed']))
            changes['source']=st.text_input('参数来源',str(row['source']))
            changes['parameter_kind']=st.selectbox('参数性质',['演示假设','实际盘点','未核实'],index=['演示假设','实际盘点','未核实'].index(row.get('parameter_kind','未核实')) if row.get('parameter_kind','未核实') in ['演示假设','实际盘点','未核实'] else 2)
            changes['verified']=st.checkbox('我已对照当日盘点和采购凭据核实以上全部参数',value=False)
            st.caption('实际来源示例：盘点表编号、盘点日期、供应商报价单编号。勾选只是用户声明，不是第三方验证。')
            if st.form_submit_button('保存该商品配置',type='primary'):
                try:
                    candidate = st.session_state.inventory.copy(deep=True)
                    for k,v in changes.items(): candidate.loc[idx,k]=v
                    apply_inventory(candidate)
                    st.success('已保存。进入补货清单即可查看更新后的建议。')
                except (ValueError, TypeError, OSError) as error:
                    st.error('未保存：' + str(error))
        inventory = st.session_state.inventory
        inventory_chart(inventory)
        inventory_view = inventory.assign(商品名称=inventory.sku.map(st.session_state.get('product_names', {})))
        table_view(chinese(inventory_view))
        st.button('下一步：查看补货建议 →',on_click=navigate,args=('补货清单',),type='primary')
    elif page=='补货清单':
        section('本次补货建议','建议下单：存在库存缺口；暂不补货：当前库存位置已达到目标；人工复核：信息不足，暂不给出数量。')
        st.caption('每次进入本页均按当前库存重新计算。请先核对参数来源，再导出清单。')
        if usage_mode == '演示探索' and st.session_state.get('is_demo'):
            st.caption('示例默认每种商品只有 10 件现货。热销商品的建议件数可能较大。真实采购请先核对现货和交期。')
        decisions=[recommend(g,effective_stock(st.session_state.inventory.set_index('sku').loc[sku].to_dict()|{'sku':sku}, st.session_state.get('purchases', []), as_of), {'require_actual':usage_mode=='实际采购准备'},as_of) if sku in set(st.session_state.inventory.sku) else dict(sku=sku,status='人工复核',qty=None,reason_codes=['MISSING_STOCK']) for sku,g in daily.groupby('sku')]
        out=pd.DataFrame(decisions)
        unverified = sum('UNVERIFIED_PARAMETERS' in r['reason_codes'] or 'MISSING_STOCK' in r['reason_codes']
                         for r in decisions)
        if usage_mode == '实际采购准备' and unverified:
            scope = '全部' if unverified == len(out) else '其中'
            st.warning(f'实际采购准备模式下，{scope} {unverified} 个商品缺少已核实的实际库存，建议数量暂留空。'
                       '请在库存配置选择「实际盘点」、填写来源并勾选核实后保存。')
            st.button('去核实库存参数', on_click=navigate, args=('库存配置',))
        st.caption('保护期是采购提前期加 1 天。库存位置是现货与可按期到货的在途之和。')
        st.button('查看字段字典', key='replenishment_dictionary', on_click=open_dictionary)
        out=out.assign(_priority=out.status.map({'人工复核':0,'建议下单':1,'暂不补货':2})).sort_values('_priority').drop(columns='_priority')
        cols=st.columns(3)
        for col,status in zip(cols,['建议下单','暂不补货','人工复核']): col.metric(status,int((out.status==status).sum()))
        out['商品名称']=out.sku.map(st.session_state.get('product_names',{})).astype(object).fillna('—')
        stocks=st.session_state.inventory.set_index('sku')
        out['现货（件）']=out.sku.map(stocks.on_hand)
        out['建议箱数']=[round(r.qty/float(stocks.loc[r.sku,'pack_size']),2) if pd.notna(r.qty) and r.sku in stocks.index and float(stocks.loc[r.sku,'pack_size'])>0 else None for r in out.itertuples()]
        out['预计库存覆盖（天）']=(out.inventory_position+out.qty).div(out['mean'].where(out['mean']>0)).round(1) if 'mean' in out else None
        st.caption('覆盖天数按现货、按期在途与本次补货合计除以历史日均销量估算；不代表到货前不会缺货。')
        search=st.text_input('查找商品编码或名称');statuses=st.multiselect('状态',list(out.status.unique()),default=list(out.status.unique()))
        filtered=out[(out.sku.str.contains(search,regex=False)|out['商品名称'].str.contains(search,regex=False))&out.status.isin(statuses)]
        if filtered.empty:
            st.info('没有符合当前筛选条件的商品，可清空搜索或调整状态。')
        else:
            statuses_in_order = [('建议下单', TEAL), ('暂不补货', SLATE), ('人工复核', AMBER)]
            counts = filtered.status.value_counts()
            distribution('筛选结果的补货状态', f'当前匹配 {len(filtered):,} 个商品',
                         [(status, int(counts.get(status, 0)), color) for status, color in statuses_in_order], '个',
                         '人工复核的商品需补齐资料后才有建议数量。上方指标统计全部商品。')
            orders = filtered[filtered.status == '建议下单'].sort_values('qty', ascending=False, kind='stable').head(12)
            if not orders.empty:
                figure = px.bar(orders, x='qty', y='sku', hover_data=['商品名称'], orientation='h',
                                labels={'sku':'商品编码','qty':'建议补货（件）'}, color_discrete_sequence=[TEAL],
                                title='建议补货件数 · 匹配商品前 12')
                figure.update_layout(bargap=.45)
                figure.update_yaxes(type='category', autorange='reversed', title=None, showgrid=False)
                show_chart(figure, '悬停可看商品名称和件数。下方明细保留筛选范围内的全部商品。', height=390)
        table_view(chinese(filtered[['sku','商品名称','status','qty','现货（件）','建议箱数','预计库存覆盖（天）']]),hide_index=True,width='stretch')
        if (out.status=='人工复核').any() and not (usage_mode == '实际采购准备' and unverified):
            st.warning('请优先处理人工复核商品，数量为空时不能按零采购理解。')
            st.button('返回库存配置处理异常',on_click=navigate,args=('库存配置',))
        ai_ui.explanation_ui(out, product_label=product_label)
        st.subheader('逐商品计算依据')
        for r in filtered.to_dict('records'):
            with st.expander(product_label(r['sku'])+' · '+('需要人工复核' if r['status']=='人工复核' else '建议 '+str(int(r['qty']))+' 件')):
                if r['status']=='人工复核':
                    for code in r.get('reason_codes',[]): st.write('• ' + reason_text(code))
                else:
                    st.write(f"建议 {int(r['qty']):,} 件；近28日日均销量约 {r['mean']:,.1f} 件，保护期 {r['protection_days']} 天。")
                    st.write(f"目标库存 {r['target']} 件，库存位置 {r['inventory_position']} 件。库存不足时，按起订量和每箱件数向上调整。")
                evidence=[{'说明字段':LABELS.get(k,k),'本次取值':(f'{v:,.2f}' if isinstance(v,float) and pd.notna(v) else str(v))} for k,v in r.items() if k!='reason_codes']
                with st.expander('查看完整计算字段'):
                    st.dataframe(pd.DataFrame(evidence), hide_index=True)
        download('导出待人工确认清单',chinese(filtered),'replenishment.csv')
    elif page=='策略回放':
        section('设置两种补货方法的对照','给两种方法同一个商品、相同的初始库存和交期，像让两家店用各自的方式备货，再对比谁缺货更少、谁积货更多。')
        st.caption('固定比例缓冲在预计需求上加 20%。历史误差缓冲根据过去少估的件数预留库存。')
        with st.expander('模拟怎么计算？'):
            st.write('每天先收货，用前一天及以前的销量决定新采购，再按当天历史销量扣库存。当天库存不足的件数记作缺货。这里使用假设库存和已成交销量，实际缺货损失不在订单里。')
        st.button('查看字段字典', key='replay_dictionary', on_click=open_dictionary)
        recent=daily[daily.date>=daily.date.max()-pd.Timedelta(days=29)]
        ranked=recent.groupby('sku').sold_qty.sum().sort_values(ascending=False).index.tolist()
        sku=st.selectbox('商品',ranked,format_func=product_label);g=daily[daily.sku==sku]
        dates=date_range('测试日期',value=((g.date.max()-pd.Timedelta(days=29)).date(),g.date.max().date()))
        lead=st.selectbox('采购提前期',[2,4,7],help='下单到到货的天数。')
        initial=st.number_input('双方初始库存',0,100000,20,help='两种方法在测试首日使用同样的现货。')
        pack=st.number_input('每箱件数',1,10000,1,help='下单量按整箱向上取整。可逐件采购时填 1。')
        selected_days=g[g.date.between(pd.Timestamp(dates[0]),pd.Timestamp(dates[1]))]
        st.caption(f'本期历史销量：{selected_days.sold_qty.sum():,.0f} 件；已确认覆盖：{int((selected_days.coverage_status=="confirmed").sum())} 天。默认按最近30天销量排序，未按策略优劣挑选。')
        if selected_days.sold_qty.sum()==0: st.warning('本期无需求，无法比较策略效果。请选择有销量的商品或日期；零需求回放仍可运行。')
        run_clicked=st.button('运行双策略回放',type='primary')
        if (run_clicked or 'replay_result' in st.session_state) and len(dates)==2:
            cfg=dict(start=str(dates[0]),end=str(dates[1]),lead_days=lead,initial_stock=initial,pack_size=pack)
            try:
                if run_clicked:
                    timeline,metrics=run_replay(g,cfg)
                    st.session_state.replay_result=(timeline,metrics,cfg,sku)
                timeline,metrics,saved_cfg,saved_sku=st.session_state.replay_result
                if cfg!=saved_cfg or sku!=saved_sku: st.warning('参数已修改。下方仍为上一次结果，请点击运行更新。')
                st.caption('已保存的本次会话实验：商品 '+saved_sku+' / '+saved_cfg['start']+' 至 '+saved_cfg['end'])
                st.subheader('策略对比结果')
                replay_reading(metrics)
                replay_comparison(metrics)
                st.button('到公平对比实验验证同一满足率目标', key='replay_to_holdout',
                          on_click=navigate, args=('公平实验室',))
                table_view(chinese(metrics),hide_index=True)
                chart=chinese(timeline)
                show_chart(px.line(chart,x='日期',y='期末现货（件）',color='补货策略',color_discrete_map=COLORS,title='期末库存轨迹'),
                           '每条线表示一种规则在每个历史日结束时剩余的现货。库存见底的日期，可与下图对应查看。')
                show_chart(px.bar(chart,x='日期',y='未满足需求（件）',color='补货策略',color_discrete_map=COLORS,barmode='group',title='每日未满足需求'),
                           '同一天并排比较两种规则。柱越高，当天模拟缺货的件数越多；本模型将缺货记在当天。')
                st.subheader('每日到货、下单与缺货明细')
                table_view(chinese(timeline),hide_index=True)
                buf=io.BytesIO()
                with zipfile.ZipFile(buf,'w') as z:
                    z.writestr('timeline.csv',csv_bytes(timeline));z.writestr('metrics.csv',csv_bytes(metrics));z.writestr('manifest.json',json.dumps(dict(config=saved_cfg,sku=saved_sku,data_hash=st.session_state.data_hash,version=VERSION,evidence_type='模拟回放，不是商户实测',inventory='假设参数',demand='历史销量代理，不含未记录的失销量'),ensure_ascii=False,indent=2))
                st.download_button('导出可复现实验 ZIP',buf.getvalue(),'experiment.zip')
            except ValueError as e: st.error(str(e))
        st.subheader('多条件稳健性检查')
        st.caption('保持商品和测试期相同，组合不同初始库存、交期和每箱件数。用并排柱对比同组内的满足率和平均库存。最多 8 组。')
        stocks=st.multiselect('比较的初始库存（件）',[0,20,100,300],default=[20,100])
        leads=st.multiselect('比较的交期（天）',[2,4,7],default=[2,7])
        packs=st.multiselect('比较的每箱件数',[1,6,12],default=[1,6])
        combination_count = len(stocks) * len(leads) * len(packs)
        st.caption(f'当前选择形成 {combination_count} 组条件。')
        scenario_cfg=dict(start=str(dates[0]),end=str(dates[1])) if len(dates)==2 else None
        if st.button('运行多条件检查'):
            try:
                if scenario_cfg is None: raise ValueError('请先选择完整测试区间')
                with st.spinner('逐条件回放，两策略使用相同输入…'):
                    scenarios=sensitivity(g,scenario_cfg,stocks,leads,packs)
                    comparisons=compare_scenarios(scenarios)
                st.session_state.scenario_result=(scenarios,comparisons,dict(sku=sku,config=scenario_cfg,stocks=stocks,leads=leads,packs=packs,data_hash=st.session_state.data_hash))
            except ValueError as e: st.error(str(e))
        if 'scenario_result' in st.session_state:
            scenarios,comparisons,scfg=st.session_state.scenario_result
            current=dict(sku=sku,config=scenario_cfg,stocks=stocks,leads=leads,packs=packs,data_hash=st.session_state.data_hash)
            if current!=scfg: st.warning('输入已变，下方保留旧条件结果；重新运行才会更新。')
            st.caption(f"结果商品 {scfg['sku']} / {scfg['config']['start']} 至 {scfg['config']['end']}")
            scenario_charts(scenarios, comparisons)
            if not comparisons.empty:
                deltas = comparisons.dropna(subset=['fill_delta'])
                if not deltas.empty:
                    figure = px.bar(deltas, x='scenario', y='fill_delta', hover_data=['inventory_delta'],
                                    labels={'scenario':'条件编号','fill_delta':'满足率差','inventory_delta':'平均库存差（件）'},
                                    title='多条件下的满足率差')
                    figure.update_xaxes(type='category')
                    figure.update_yaxes(tickformat='.1%')
                    figure.update_traces(hovertemplate='第 %{x} 组<br>满足率差 %{y:+.1%}<br>平均库存差 %{customdata[0]:+,.1f} 件')
                    figure.add_hline(y=0, line_color='#667c76')
                    show_chart(figure, '差值为历史误差缓冲减固定比例缓冲。悬停可看同一条件的平均库存差。', height=300)
            table_view(chinese(scenarios),hide_index=True)
            table_view(comparisons.rename(columns={'scenario':'条件编号','fill_delta':'满足率差（自适应减基线）','inventory_delta':'平均库存差（件）','conclusion':'本条件观察'}),hide_index=True)
            package=io.BytesIO()
            with zipfile.ZipFile(package,'w') as z:
                z.writestr('scenario_metrics.csv',csv_bytes(scenarios))
                z.writestr('comparisons.csv',csv_bytes(comparisons))
                z.writestr('manifest.json',json.dumps(dict(**scfg,evidence_type='模拟敏感性实验，不能替代真实商户验证',lost_sales='原始订单未提供真实缺货失销量'),ensure_ascii=False,indent=2))
            st.download_button('导出全部条件与实验说明',package.getvalue(),'sensitivity-experiment.zip')
