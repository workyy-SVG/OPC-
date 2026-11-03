from pathlib import Path
import hashlib,json,io,zipfile
from ui import table_view, date_range, apply_theme
import pandas as pd
import streamlit as st
import plotly.express as px
from core.ingest import parse_orders
from core.demand import build_daily
from core.policy import recommend
from core.replay import run_replay
from core.export import csv_bytes
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
st.sidebar.title('📦 小店补货助手')
st.sidebar.caption('让每一件补货，都有据可查')
def navigate(target): st.session_state['navigation']=target
page=st.sidebar.radio('经营导航',['经营总览','导入与质检','库存配置','补货清单','策略回放','使用指南','数据与说明'],key='navigation')
st.sidebar.info('单门店 · 整数件 · 每日复核\n\n建议需人工确认后采购')
st.title(page)
HEADINGS={
'经营总览':('今天的经营，从这里开始','先了解数据与库存准备情况，再处理补货任务。','经营概览'),
'导入与质检':('先把销售记录整理清楚','导入、匹配、核验，确保每一条建议都有可靠输入。','数据准备'),
'库存配置':('把真实库存和采购条件填准确','逐商品核对现货、在途与交期，为补货决策做好准备。','库存管理'),
'补货清单':('先处理异常，再确认采购','检查人工复核事项，逐条核对建议依据，导出待确认清单。','采购决策'),
'策略回放':('在同样的条件下，看清策略取舍','同时观察满足率与库存占用，保留可追溯的实验记录。','经营分析'),
'使用指南':('第一次使用，也能按步骤完成','从五分钟演示开始，随时查阅字段解释和操作帮助。','帮助中心'),
'数据与说明':('知道数据从哪里来，结果适用于哪里','了解样本来源、计算规则与当前产品边界。','数据说明')}
headline,subtitle,tag=HEADINGS[page]
st.markdown(f'<div class="workspace-banner"><span class="banner-tag">小店补货助手 / {tag}</span><h2>{headline}</h2><p>{subtitle}</p><div class="banner-flow">数据准备　／　库存管理　／　采购决策　／　经营分析</div></div>',unsafe_allow_html=True)

with st.sidebar:
    st.divider()
    st.subheader('当前进度')
    st.write('✅ 已完成数据核验' if 'daily' in st.session_state else '① 等待导入销售数据')
    st.write('✅ 已建立库存配置' if 'inventory' in st.session_state else '② 等待填写库存')
    st.caption('首次使用？打开「使用指南」，按步骤体验完整流程。')
LABELS={'order_id':'订单编号','sku':'商品编码','order_at':'销售日期','qty':'数量（件）','line_id':'订单行编号','status':'处理状态','on_hand':'现货数量（件）','on_order':'在途数量（件）','as_of':'库存快照日期','lead_days':'采购提前期（天）','pack_size':'每箱件数','min_order':'最小起订量（件）','arrival_confirmed':'确认在途按期到货','source':'参数来源','row_no':'原文件行号','reason_code':'问题类型','hint':'修正建议','policy':'补货策略','date':'日期','demand':'需求代理（件）','arrived':'当日到货（件）','order':'当日下单（件）','ending_stock':'期末现货（件）','shortage':'未满足需求（件）','review':'是否人工复核','fill_rate':'件数满足率','mean_stock':'平均期末库存（件）','stockout_rate':'缺货天数占比','review_rate':'人工复核率','orders':'下单次数','final_pipeline':'期末在途（件）','common_eligible_rate':'双方可自动建议日期占比','inventory_position':'库存位置（件）','forecast_lead':'交期内预测需求（件）','buffer':'误差缓冲（件）','target':'目标库存（件）','mean':'近28日日均销量','protection_days':'保护期（天）','window_start':'历史窗口起始日','window_end':'历史窗口结束日','sample_count':'有效历史天数','error_samples':'误差窗口数量','rule_version':'规则版本','run_id':'运行编号','reason_codes':'复核原因'}
REASONS={'INVALID_FIELD':'必填内容缺失、日期格式不匹配或数量不是整数','CONFLICTING_LINE_ID':'同一订单行编号对应不同内容，请核对原订单','DUPLICATE_LINE':'重复订单行，已剔除','CANCELLED':'取消订单，不计入销量','RETURN':'退货单独统计，不冲减需求','NOT_COMPLETED':'未完成或零数量订单，不计入销量','NON_PRODUCT':'非商品记录，已排除','SUSPECT_DUPLICATE':'疑似重复但缺少行编号，保留并请人工核对','HISTORY_GAP':'最近28天覆盖不完整，请补齐销售记录并确认覆盖','INSUFFICIENT_ERROR_WINDOWS':'完整误差窗口不足20个，请增加更早的销售历史','ARRIVAL_UNCONFIRMED':'在途到货时间尚未确认，请在库存配置中核对','SNAPSHOT_DATE_MISMATCH':'库存日期与决策日期不一致，请更新库存快照','MISSING_STOCK':'缺少该商品库存，请导入库存配置'}
def reason_text(code):
    return REASONS.get(code, '请检查'+LABELS.get(code.removeprefix('INVALID_').lower(),code)+'是否为有效非负整数')
def chinese(df):
    result=df.copy()
    if 'policy' in result: result['policy']=result.policy.replace({'baseline':'固定比例缓冲','adaptive':'历史误差缓冲'})
    if 'reason_code' in result: result['reason_code']=result.reason_code.map(reason_text)
    if 'status' in result: result['status']=result.status.replace({'completed':'已完成','complete':'已完成','cancelled':'已取消','canceled':'已取消'})
    for col in ['arrival_confirmed','review']:
        if col in result: result[col]=result[col].map(lambda v:'是' if v is True or str(v)=='True' else '否' if v is False or str(v)=='False' else v)
    if 'reason_codes' in result: result['reason_codes']=result.reason_codes.map(lambda codes:'；'.join(reason_text(c) for c in codes) if isinstance(codes,list) else codes)
    return result.rename(columns=LABELS|{'unit_price':'单价','description':'商品描述'})
def section(title,description):
    st.subheader(title);st.write(description)

def download(label,df,name): st.download_button(label,csv_bytes(df),name,'text/csv')
if page=='经营总览':
    ready='daily' in st.session_state and not st.session_state.daily.empty
    cols=st.columns(4)
    cols[0].metric('管理商品',st.session_state.daily.sku.nunique() if ready else '—',help='来自当前已核验数据')
    cols[1].metric('有效销售记录',st.session_state.get('summary',{}).get('valid','—'))
    cols[2].metric('待核对记录',len(st.session_state.issues) if 'issues' in st.session_state else '—')
    cols[3].metric('库存配置商品',len(st.session_state.inventory) if 'inventory' in st.session_state else '—')
    left,right=st.columns([1.5,1])
    with left:
        section('我的工作任务','按顺序完成准备，再查看补货建议。统计仅基于当前会话数据。')
        tasks=[('01','整理销售数据','导入并核验完整日期内的销售记录。','导入与质检',ready),('02','核对库存参数','填写真实现货、在途及供应商交期。','库存配置','inventory' in st.session_state),('03','审核补货建议','先解决人工复核，再检查下单数量。','补货清单',False),('04','比较历史策略','查看缺货与库存之间的取舍。','策略回放','replay_result' in st.session_state)]
        for number,title,body,target,done in tasks:
            with st.container(border=True):
                st.markdown(f'**{number}　{title}**　'+('· 已有数据' if done else '· 待处理'))
                st.caption(body)
                st.button('打开'+target,key='home_'+target,on_click=navigate,args=(target,),disabled=target not in ['导入与质检'] and not ready)
    with right:
        with st.container(border=True):
            st.subheader('建议下一步')
            target='导入与质检' if not ready else '库存配置' if 'inventory' not in st.session_state else '补货清单'
            st.write('当前建议：'+target)
            st.button('继续处理 →',type='primary',on_click=navigate,args=(target,))
            st.caption('库存中预填的数字仅为演示参数。已有配置不代表完成真实盘点。')
        with st.container(border=True):
            st.subheader('数据范围')
            if ready:
                st.write(str(st.session_state.daily.date.min().date())+' 至 '+str(st.session_state.daily.date.max().date()))
                st.caption('当前数据指纹：'+st.session_state.data_hash[:12])
            else: st.info('尚未导入数据；可先使用内置 UCI 样本体验。')
        st.button('查看五分钟使用指南',on_click=navigate,args=('使用指南',))
elif page=='导入与质检':
    st.info('第一次体验：点击下方「一键加载 UCI 真实交易样本」，保持日期与字段默认值，勾选覆盖确认，再点击「核验并使用此数据」。')
    section('第一步 · 选择销售数据','上传从收银系统或记账表导出的 CSV 文件，或使用UCI 官方数据中筛选的 20 个商品真实交易。该数据来自英国在线零售商，库存仍为演示参数。')
    with st.expander('我的表格需要包含哪些字段？'):
        st.markdown('**必填：**订单编号、商品编码、销售日期、销售数量。商品编码是每种商品的唯一编号，例如 00001。数量必须是整数。\n\n**选填：**订单行编号、单价、商品描述、订单状态。原表列名可以不同，下一步可手动匹配。\n\n取消订单和退货会单独统计；只有已完成的正数量销售进入需求计算。')
    upload=st.file_uploader('上传 CSV（订单号和商品编码 将保留前导零）',type='csv')
    demo=st.button('一键加载 UCI 真实交易样本',type='primary')
    st.caption('来源：UCI Online Retail II · 陈大庆 · CC BY 4.0。订单来自真实公开交易；默认库存不是真实库存。')
    if demo or upload:
        raw=Path('data/demo/orders.csv').read_bytes() if demo else upload.getvalue()
        if st.session_state.get('raw')!=raw:
            for dependent in ['daily','inventory','issues','summary','replay_result','data_hash']:
                st.session_state.pop(dependent,None)
        st.session_state['raw']=raw
    if 'raw' in st.session_state:
        try: df=pd.read_csv(io.BytesIO(st.session_state.raw),dtype=str,keep_default_na=False)
        except Exception as e: st.error(str(e));st.stop()
        section('第二步 · 检查字段与日期','以下为前 10 条原始记录。请将表格中的四个必填列逐一对应；同一列不能重复选择。')
        table_view(chinese(df.head(10)),hide_index=True)
        mapping={}
        for key in ['order_id','sku','order_at','qty']:
            selected=st.selectbox('对应字段：'+({'qty':'销售数量（件）'}|LABELS).get(key,key),list(df.columns),index=list(df.columns).index(key) if key in df else 0,key='map_'+key,format_func=lambda c: LABELS.get(c,c));mapping[selected]=key
        fmt=st.selectbox('日期格式',['%Y-%m-%d','%d/%m/%Y','%m/%d/%Y','%Y-%m-%d %H:%M:%S'],format_func=lambda f:{'%Y-%m-%d':'年-月-日，例如2025-01-31','%d/%m/%Y':'日/月/年，例如31/01/2025','%m/%d/%Y':'月/日/年，例如01/31/2025','%Y-%m-%d %H:%M:%S':'年月日与时分秒，例如2025-01-31 09:30:00'}[f])
        section('第三步 · 确认完整覆盖区间','请选择每天均完整导出的营业日期。区间内没有订单的日期会按零销量处理；请勿将未导出的日期当作零销量。UCI 样本覆盖 2010-01-01 至 2010-06-30；把完整交易导出区间视为连续覆盖是回放假设，不等同于已验证营业日。')
        coverage=date_range('已完整覆盖的营业日期区间',value=(pd.Timestamp('2010-01-01').date(),pd.Timestamp('2010-06-30').date()))
        confirmed=st.checkbox('我确认该区间每天完整导出；无订单日期可视为零销量')
        if st.button('核验并使用此数据',disabled=not confirmed):
            try:
                if len(mapping)!=4 or len(coverage)!=2: raise ValueError('请使用四个不同字段并选定完整日期区间')
                clean,issues,summary=parse_orders(df,mapping,fmt)
                daily=build_daily(clean,pd.date_range(*coverage))
                st.session_state.update(daily=daily,issues=issues,summary=summary,data_hash=hashlib.sha256(st.session_state.raw).hexdigest())
                st.session_state.pop('inventory',None)
            except Exception as e: st.error(str(e))
        if 'summary' in st.session_state:
            section('第四步 · 查看核验结果','错误记录已隔离。疑似重复记录仍保留，请根据问题清单核对，修正文件后重新导入。')
            names={'valid':'有效销售','cancelled':'取消订单','returned':'退货记录','suspicious':'疑似重复（已保留）','error':'错误记录','excluded':'非商品记录','duplicate':'已剔除重复'}
            cols=st.columns(4)
            for i,(k,v) in enumerate(st.session_state.summary.items()): cols[i%4].metric(names[k],v)
            if st.session_state.issues.empty: st.success('未发现问题记录。下一步：在左侧打开「库存配置」，核对实际现货和交期。')
            else: table_view(chinese(st.session_state.issues),hide_index=True)
            download('下载问题行',chinese(st.session_state.issues),'issues.csv')
            st.button('下一步：核对库存 →',on_click=navigate,args=('库存配置',))
elif page=='使用指南':
    st.success('建议先用内置样本体验一遍，再替换为自己的订单和库存。整个体验约需 5 分钟。')
    tabs=st.tabs(['快速上手','字段字典','如何理解结果','常见问题'])
    with tabs[0]:
        for title,body in [
            ('1 · 导入与质检','点击「一键加载 UCI 真实交易样本」。保持四项字段映射、日期格式及日期区间默认值，勾选完整覆盖确认，点击「核验并使用此数据」。查看有效销售与问题记录。'),
            ('2 · 库存配置','左侧进入库存配置。选择商品后填写中文表单：现货填当前可卖数量，在途填已下单未到货数量，交期填下单到到货的天数。默认数字都是演示值，真实使用时必须替换。'),
            ('3 · 补货清单','查看三类结果：建议下单、暂不补货、人工复核。展开某商品的计算依据，检查目标库存、现货和在途，然后下载待人工确认的清单。'),
            ('4 · 策略回放','选择一个商品，保持默认测试日期，选择交期并填写初始库存。点击运行，比较两种策略的满足率与库存占用。下载实验 ZIP 可保留逐日记录和配置。')]:
            with st.container(border=True): section(title,body)
        st.info('演示验证：先记住某商品建议数量，再到库存配置增加现货，返回清单观察数量变化。取消「确认在途按期到货」，该商品会转为人工复核。')
    with tabs[1]:
        table_view(pd.DataFrame([
            ['现货数量','当前仓库中可以销售的商品数量。','8 件'],['在途数量','已经采购、尚未入库且预计按期到达的数量。','4 件'],['采购提前期','下单到开门前到货之间的天数，至少为1。','3 天'],['每箱件数','采购数量需要满足的整数倍。','6 件/箱'],['最小起订量','供应商要求的最低采购数量；没有则填0。','15 件'],['库存快照日期','库存盘点对应的决策日期，必须与页面提示一致。','演示为2010-07-01'],['保护期','采购提前期加上1天复核间隔。','交期3天 → 保护期4天'],['库存位置','现货与按期在途之和。','8 + 4 = 12 件']
        ],columns=['字段','填写或计算方法','示例']))
    with tabs[2]:
        st.markdown('### 为什么建议补货18件？\n近28日日均销量为5件，交期3天，保护期4天，误差缓冲6件：\n\n1. 目标库存 = 向上取整（5 × 4 + 6）= 26件。\n2. 库存位置 = 现货8件 + 按期在途4件 = 12件。\n3. 缺口 = 26 − 12 = 14件。\n4. 起订量15件、每箱6件，所以向上取整为18件。')
        st.warning('人工复核的数量为空，不等于建议0件。请先补齐相关信息再生成建议。')
        st.markdown('**件数满足率：**模拟满足的需求件数占比，越高代表缺货越少。\n\n**平均期末库存：**每天营业结束后的平均存货，越高通常意味着占用更多库存。应与满足率一起比较。\n\n**固定比例缓冲：**在预测保护期需求上增加20%。**历史误差缓冲：**依据历史预测误差确定缓冲，两者共享相同回放条件。')
    with tabs[3]:
        for q,a in [('为什么全部需要人工复核？','检查库存日期和交期，确认在途可按期到货。最近28天必须完整，还需至少20个已经结束的历史误差窗口；建议准备90天以上连续数据。'),('为什么上传后还没有建议？','上传只是读取文件，还需确认字段和日期区间，勾选覆盖确认并点击核验。然后进入库存配置。'),('能直接用Excel文件吗？','当前页面接收CSV。请在Excel中另存为CSV UTF-8，并将订单号、商品编码保留为文本。'),('关闭或刷新页面数据会保存吗？','数据主要保存在当前会话中。请及时下载库存模板、清单和实验结果；新会话需要重新导入。'),('会自动向供应商下单吗？','不会。导出的清单须由经营者审核后自行采购。'),('回放结果是否代表真实利润改善？','不代表。需求来自历史成交量，库存和缺货是模拟值；本工具尚未使用真实商户数据验证。')]:
            with st.expander(q): st.write(a)
elif page=='数据与说明':
    st.markdown(Path('DATA_SOURCES.md').read_text(encoding='utf-8'))
    manifest=Path('data/demo/manifest.json').read_bytes()
    st.download_button('下载样本来源与处理记录',manifest,'数据来源与处理记录.json','application/json')
    st.markdown('规则版本 **1.0.0**。保护期 = 交期 + 1；28 天均值；最多 60 个历史误差窗口的 最近秩法 90% 分位数；至少 20 个误差窗口。')
    st.warning('历史成交量只是需求代理。回放为丢失销售模拟，不能推断真实商户改善幅度。经营需求尚待商户验证。')
else:
    if 'daily' not in st.session_state: st.info('请先在导入与质检页面加载并确认数据。');st.stop()
    daily=st.session_state.daily
    if daily.empty: st.warning('没有可用商品');st.stop()
    as_of=daily[daily.coverage_status=='confirmed'].date.max()+pd.Timedelta(days=1)
    if 'inventory' not in st.session_state:
        st.session_state.inventory=pd.DataFrame([dict(sku=s,as_of=str(as_of.date()),on_hand=10,on_order=0,lead_days=3,pack_size=6,min_order=0,arrival_confirmed=True,source='合成演示参数') for s in daily.sku.unique()])
    if page=='库存配置':
        st.caption('02 / 库存快照必须与决策日期一致：'+str(as_of.date()))
        section('填写商品库存与采购约束','选择商品，修改下方字段并保存。保存后进入补货清单即可重新计算。商品编码不能编辑；库存快照日期请使用上方提示日期。')
        st.warning('初始库存是演示参数。实际使用请逐项核对现货、在途与供应商交期。')
        with st.expander('填写说明与批量导入步骤',expanded=True):
            st.markdown('**现货/在途：**均为非负整数；在途只填写预计按期到达的采购。**交期：**至少1天。**每箱件数：**至少1件。**起订量：**没有限制时填0。\n\n批量修改：下载库存模板 → 保留表头和商品编码 → 修改数量和日期 → 上传 → 点击应用。表格中的到货确认填写“是”或“否”。')
        template=st.session_state.inventory
        download('下载库存模板',chinese(template),'inventory.csv')
        f=st.file_uploader('批量导入库存 CSV',type='csv')
        if f and st.button('应用库存文件'):
            try:
                inv=pd.read_csv(f,dtype=str).rename(columns={v:k for k,v in LABELS.items()});inv['arrival_confirmed']=inv['arrival_confirmed'].map({'是':True,'否':False,'True':True,'False':False});assert set(template.columns)<=set(inv.columns);assert not inv.sku.duplicated().any();st.session_state.inventory=inv
            except Exception: st.error('库存字段不完整或商品编码重复')
        chosen=st.selectbox('选择要修改的商品',st.session_state.inventory.sku.tolist())
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
            if st.form_submit_button('保存该商品配置',type='primary'):
                for k,v in changes.items(): st.session_state.inventory.loc[idx,k]=v
                st.success('已保存。进入补货清单即可查看更新后的建议。')
        table_view(chinese(st.session_state.inventory))
        st.button('下一步：查看补货建议 →',on_click=navigate,args=('补货清单',),type='primary')
    elif page=='补货清单':
        section('本次补货建议','建议下单：存在库存缺口；暂不补货：当前库存位置已达到目标；人工复核：信息不足，暂不给出数量。')
        st.caption('每次进入本页均按当前库存重新计算。请先核对参数来源，再导出清单。')
        decisions=[recommend(g,st.session_state.inventory.set_index('sku').loc[sku].to_dict()|{'sku':sku}, {},as_of) if sku in set(st.session_state.inventory.sku) else dict(sku=sku,status='人工复核',qty=None,reason_codes=['MISSING_STOCK']) for sku,g in daily.groupby('sku')]
        out=pd.DataFrame(decisions)
        out=out.assign(_priority=out.status.map({'人工复核':0,'建议下单':1,'暂不补货':2})).sort_values('_priority').drop(columns='_priority')
        cols=st.columns(3)
        for col,status in zip(cols,['建议下单','暂不补货','人工复核']): col.metric(status,int((out.status==status).sum()))
        search=st.text_input('查找商品编码');statuses=st.multiselect('状态',list(out.status.unique()),default=list(out.status.unique()))
        filtered=out[out.sku.str.contains(search,regex=False)&out.status.isin(statuses)]
        table_view(chinese(filtered[['sku','status','qty']]),hide_index=True,use_container_width=True)
        if (out.status=='人工复核').any():
            st.warning('请优先处理人工复核商品，数量为空时不能按零采购理解。')
            st.button('返回库存配置处理异常',on_click=navigate,args=('库存配置',))
        st.subheader('逐商品计算依据')
        for r in filtered.to_dict('records'):
            with st.expander('商品 '+str(r['sku'])+' · '+('需要人工复核' if r['status']=='人工复核' else '建议 '+str(int(r['qty']))+' 件')):
                if r['status']=='人工复核':
                    for code in r.get('reason_codes',[]): st.warning(reason_text(code))
                else:
                    st.write(f"目标库存 {r['target']} 件 − 库存位置 {r['inventory_position']} 件，再按起订量和包装倍数向上调整，得到建议数量。")
                evidence=[{'说明字段':LABELS.get(k,k),'本次取值':str(v)} for k,v in r.items() if k!='reason_codes']
                table_view(pd.DataFrame(evidence))
        download('导出待人工确认清单',chinese(filtered),'replenishment.csv')
    elif page=='策略回放':
        section('设置公平的对照实验','使用同一商品、同一测试日期、同一初始库存与交期，对比固定比例缓冲和历史误差缓冲。测试前需保留足够历史作为预热。')
        with st.expander('回放如何计算？',expanded=True):
            st.write('每天依次执行：接收当天到货 → 仅使用昨天及以前的销售生成建议 → 登记未来到货 → 按当天需求扣减库存。未满足需求不结转；人工复核当天不新增采购。')
        sku=st.selectbox('商品',daily.sku.unique());g=daily[daily.sku==sku]
        dates=date_range('测试日期',value=((g.date.max()-pd.Timedelta(days=29)).date(),g.date.max().date()))
        lead=st.selectbox('采购提前期',[2,4,7]);initial=st.number_input('双方初始库存',0,100000,20);pack=st.number_input('包装倍数',1,10000,1)
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
                st.info('先看件数满足率，再看平均库存：满足率高但库存也高，说明减少缺货的同时增加了库存占用。比例指标0.95表示95%。')
                table_view(chinese(metrics),hide_index=True)
                chart=chinese(timeline)
                st.plotly_chart(px.line(chart,x='日期',y='期末现货（件）',color='补货策略',title='期末库存轨迹'),use_container_width=True,config={"displayModeBar":False})
                st.plotly_chart(px.bar(chart,x='日期',y='未满足需求（件）',color='补货策略',barmode='group',title='每日未满足需求'),use_container_width=True,config={'displayModeBar':False})
                st.subheader('每日到货、下单与缺货明细')
                table_view(chinese(timeline),hide_index=True)
                buf=io.BytesIO()
                with zipfile.ZipFile(buf,'w') as z:
                    z.writestr('timeline.csv',csv_bytes(timeline));z.writestr('metrics.csv',csv_bytes(metrics));z.writestr('manifest.json',json.dumps(dict(config=saved_cfg,sku=saved_sku,data_hash=st.session_state.data_hash,version='1.0.0'),ensure_ascii=False,indent=2))
                st.download_button('导出可复现实验 ZIP',buf.getvalue(),'experiment.zip')
            except ValueError as e: st.error(str(e))
