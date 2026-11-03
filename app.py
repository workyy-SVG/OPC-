from pathlib import Path
import hashlib,json,io,zipfile
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
st.sidebar.title('📦 小店补货助手')
st.sidebar.caption('让每一件补货，都有据可查')
page=st.sidebar.radio('工作台',['导入与质检','库存配置','补货清单','策略回放','数据与说明'])
st.sidebar.info('单门店 · 整数件 · 每日复核\n\n建议需人工确认后采购')
st.title(page)
def download(label,df,name): st.download_button(label,csv_bytes(df),name,'text/csv')
if page=='导入与质检':
    st.caption('01 / 导入订单 → 确认覆盖 → 检查质量')
    upload=st.file_uploader('上传 CSV（订单号和 SKU 将保留前导零）',type='csv')
    demo=st.button('一键加载合成演示样本',type='primary')
    if demo or upload:
        raw=Path('data/demo/orders.csv').read_bytes() if demo else upload.getvalue()
        st.session_state['raw']=raw
    if 'raw' in st.session_state:
        try: df=pd.read_csv(io.BytesIO(st.session_state.raw),dtype=str,keep_default_na=False)
        except Exception as e: st.error(str(e));st.stop()
        st.dataframe(df.head(10),hide_index=True)
        mapping={}
        for key in ['order_id','sku','order_at','qty']:
            selected=st.selectbox('字段映射：'+key,list(df.columns),index=list(df.columns).index(key) if key in df else 0,key='map_'+key);mapping[selected]=key
        fmt=st.selectbox('日期格式',['%Y-%m-%d','%d/%m/%Y','%m/%d/%Y','%Y-%m-%d %H:%M:%S'])
        coverage=st.date_input('已完整覆盖的营业日期区间',value=(pd.Timestamp('2025-01-01').date(),pd.Timestamp('2025-05-30').date()))
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
            st.json(st.session_state.summary);st.dataframe(st.session_state.issues);download('下载问题行',st.session_state.issues,'issues.csv')
elif page=='数据与说明':
    st.markdown(Path('DATA_SOURCES.md').read_text(encoding='utf-8'))
    st.markdown('规则版本 **1.0.0**。保护期 = 交期 + 1；28 天均值；最多 60 个历史误差窗口的 nearest-rank 90% 分位数；至少 20 个误差窗口。')
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
        template=st.session_state.inventory
        download('下载库存模板',template,'inventory.csv')
        f=st.file_uploader('批量导入库存 CSV',type='csv')
        if f and st.button('应用库存文件'):
            try:
                inv=pd.read_csv(f,dtype={'sku':str,'as_of':str});assert set(template.columns)<=set(inv.columns);assert not inv.sku.duplicated().any();st.session_state.inventory=inv
            except Exception: st.error('库存字段不完整或 SKU 重复')
        st.session_state.inventory=st.data_editor(st.session_state.inventory,hide_index=True,disabled=['sku'],key='inventory_editor')
    elif page=='补货清单':
        st.caption('03 / 每次展示均按当前库存与规则重新计算')
        decisions=[recommend(g,st.session_state.inventory.set_index('sku').loc[sku].to_dict()|{'sku':sku}, {},as_of) if sku in set(st.session_state.inventory.sku) else dict(sku=sku,status='人工复核',qty=None,reason_codes=['MISSING_STOCK']) for sku,g in daily.groupby('sku')]
        out=pd.DataFrame(decisions); cols=st.columns(3)
        for col,status in zip(cols,['建议下单','暂不补货','人工复核']): col.metric(status,int((out.status==status).sum()))
        search=st.text_input('查找 SKU');statuses=st.multiselect('状态',list(out.status.unique()),default=list(out.status.unique()))
        filtered=out[out.sku.str.contains(search,regex=False)&out.status.isin(statuses)]
        st.dataframe(filtered[['sku','status','qty']],hide_index=True,use_container_width=True)
        for r in filtered.to_dict('records'):
            with st.expander(str(r['sku'])+' · 为什么是 '+str(r['qty'])+' 件？'): st.json(r);st.caption('复核原因：补齐库存/交期，确认到货时间，或导入连续完整历史后重新计算。')
        download('导出待人工确认清单',filtered,'replenishment.csv')
    elif page=='策略回放':
        sku=st.selectbox('商品',daily.sku.unique());g=daily[daily.sku==sku]
        dates=st.date_input('测试日期',value=((g.date.max()-pd.Timedelta(days=29)).date(),g.date.max().date()))
        lead=st.selectbox('采购提前期',[2,4,7]);initial=st.number_input('双方初始库存',0,100000,20);pack=st.number_input('包装倍数',1,10000,1)
        if st.button('运行双策略回放',type='primary') and len(dates)==2:
            cfg=dict(start=str(dates[0]),end=str(dates[1]),lead_days=lead,initial_stock=initial,pack_size=pack)
            try:
                timeline,metrics=run_replay(g,cfg);st.dataframe(metrics,hide_index=True)
                st.plotly_chart(px.line(timeline,x='date',y='ending_stock',color='policy',title='期末库存轨迹'),use_container_width=True)
                st.plotly_chart(px.bar(timeline,x='date',y='shortage',color='policy',barmode='group',title='每日未满足需求'),use_container_width=True)
                st.dataframe(timeline,hide_index=True)
                buf=io.BytesIO()
                with zipfile.ZipFile(buf,'w') as z:
                    z.writestr('timeline.csv',csv_bytes(timeline));z.writestr('metrics.csv',csv_bytes(metrics));z.writestr('manifest.json',json.dumps(dict(config=cfg,sku=sku,data_hash=st.session_state.data_hash,version='1.0.0'),ensure_ascii=False,indent=2))
                st.download_button('导出可复现实验 ZIP',buf.getvalue(),'experiment.zip')
            except ValueError as e: st.error(str(e))
