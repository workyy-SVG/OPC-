import hashlib
import pandas as pd
import streamlit as st

def table_view(df, **kwargs):
    """中文表格操作；不使用带英文菜单的原生网格。"""
    key=hashlib.md5(('|'.join(map(str,df.columns))+str(len(df))+df.head(1).to_csv(index=False)).encode()).hexdigest()
    frame=df.copy()
    with st.expander('表格工具 · 排序、筛选显示列与统计'):
        a,b=st.columns(2)
        field=a.selectbox('排序字段',['保持原顺序']+list(frame.columns),key=key+'sort')
        direction=b.radio('排列方向',['升序','降序'],horizontal=True,key=key+'direction')
        columns=st.multiselect('显示哪些列',list(frame.columns),default=list(frame.columns),key=key+'cols',placeholder='请选择需要显示的列')
        if field!='保持原顺序': frame=frame.sort_values(field,ascending=direction=='升序',kind='stable')
        numeric=frame.select_dtypes(include='number')
        if not numeric.empty:
            col=st.selectbox('查看数值统计',list(numeric.columns),key=key+'stats')
            st.caption(f'合计：{numeric[col].sum():,.2f}　平均：{numeric[col].mean():,.2f}　最小：{numeric[col].min():,.2f}　最大：{numeric[col].max():,.2f}')
    count=len(frame);pages=max(1,(count+19)//20)
    if pages>1:
        page=st.number_input('页码（每页20条）',1,pages,1,key=key+'page')
        frame=frame.iloc[(page-1)*20:page*20]
    st.caption(f'共 {count} 条记录'+(' · 可横向滚动查看完整字段' if len(columns)>5 else ''))
    if not columns: st.info('请在表格工具中至少选择一列。');return
    html=frame[columns].fillna('—').to_html(index=False,escape=True,border=0,classes='cn-table')
    st.markdown('<div class="table-shell">'+html+'</div>',unsafe_allow_html=True)

def date_range(label,value):
    st.write(label)
    a,b=st.columns(2)
    start=a.text_input('开始日期（年-月-日）',str(value[0]),key=label+'start')
    end=b.text_input('结束日期（年-月-日）',str(value[1]),key=label+'end')
    try:
        first=pd.to_datetime(start,format='%Y-%m-%d').date();last=pd.to_datetime(end,format='%Y-%m-%d').date()
        if first>last: raise ValueError()
        return first,last
    except ValueError:
        st.error('请按年-月-日填写日期，且开始日期不能晚于结束日期。');st.stop()

def apply_theme():
    st.markdown('''<style>
.stApp{background:radial-gradient(ellipse at 92% 4%,#cdebe780,transparent 38%),radial-gradient(ellipse at 4% 55%,#f7e5c880,transparent 36%),linear-gradient(135deg,#f3f7f5,#f2f5fc 65%,#faf6ef)!important}
[data-testid="stHeader"]{background:transparent}
[data-testid="stSidebar"]{background:linear-gradient(165deg,#e4f0eb,#eef0fa 70%,#f7edde)!important}
.workspace-banner{position:relative;overflow:hidden;padding:32px 38px;margin:12px 0 28px;border-radius:22px;background:radial-gradient(circle at 95% 20%,#ffffff24 0 100px,transparent 101px),radial-gradient(circle at 78% 110%,#a5ddcd30 0 160px,transparent 161px),linear-gradient(115deg,#163f3c,#287b70);box-shadow:0 14px 35px #1b55451a;color:white}
.workspace-banner h2{color:white;font-size:30px;margin:12px 0 6px}.workspace-banner p{color:#d9eee7}.banner-tag{font-size:12px;letter-spacing:3px;color:#e3c88f}.banner-flow{font-size:14px;border-top:1px solid #ffffff30;padding-top:16px;margin-top:18px;color:#e5f6f0}
[data-testid="stMetric"]{background:linear-gradient(135deg,#fff,#eef8f4)!important;border-top:3px solid #4d9f8d!important}
[data-testid="stExpander"],[data-testid="stForm"]{background:#ffffffd9;border:1px solid #dce6e2!important;border-radius:14px;box-shadow:0 5px 18px #345e4a05}
.table-shell{overflow-x:auto;border:1px solid #dce7e2;border-radius:12px;background:white;margin-bottom:20px;box-shadow:0 5px 20px #234a4208}.cn-table{width:100%;border-collapse:collapse;font-size:14px;white-space:nowrap}.cn-table th{text-align:left!important;background:#e4f0eb;color:#245348;font-weight:600;padding:13px 18px;border-bottom:1px solid #cfded6}.cn-table td{padding:12px 18px;border-bottom:1px solid #edf1ef}.cn-table tr:nth-child(even){background:#f7faf9}.cn-table tr:hover{background:#eaf5ef}
[data-testid="stFileUploaderDropzoneInstructions"]{font-size:0!important}
[data-testid="stFileUploaderDropzoneInstructions"]>div,[data-testid="stFileUploaderDropzoneInstructions"] span,[data-testid="stFileUploaderDropzoneInstructions"] small{display:none!important}
[data-testid="stFileUploaderDropzoneInstructions"]:after{content:'将表格文件拖到这里，或点击选择文件';font-size:14px;color:#426257}
[data-testid="stFileUploaderDropzone"] button{font-size:0!important}
[data-testid="stFileUploaderDropzone"] button:after{content:'选择文件';font-size:14px}
[data-testid="stFileUploaderDropzone"] button div{display:none!important}
#MainMenu{display:none}[data-testid="stToolbar"]{display:none}
/* 经营工作台：网格底纹、深色导航与多色业务卡片 */
.stApp{background:radial-gradient(ellipse at 85% 8%,#bce4e4a0,transparent 40%),radial-gradient(ellipse at 5% 65%,#f0d3ac65,transparent 38%),repeating-linear-gradient(0deg,transparent 0 31px,#617e9410 32px),repeating-linear-gradient(90deg,transparent 0 31px,#617e9410 32px),#f1f4f8!important}
[data-testid="stSidebar"]{background:linear-gradient(165deg,#112e37,#193e48 65%,#254c54)!important;border-right:1px solid #ffffff10}
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3,[data-testid="stSidebar"] p,[data-testid="stSidebar"] label,[data-testid="stSidebar"] [data-testid="stCaptionContainer"]{color:#e2eeef!important}
[data-testid="stSidebar"] [data-testid="stAlert"]{background:#ffffff0e;border:1px solid #ffffff20}
[data-testid="stSidebar"] [role="radiogroup"]{gap:8px}
[data-testid="stSidebar"] [role="radiogroup"] label{padding:10px 12px;border-radius:9px;background:#ffffff08}
[data-testid="stSidebar"] [role="radiogroup"] label:hover{background:#ffffff19}
.workspace-banner{background:linear-gradient(120deg,#122f3b 0%,#245d70 60%,#438b94 100%);border:1px solid #ffffff80;box-shadow:0 18px 45px #193a4d20;padding:26px 32px}
.workspace-banner:after{content:'';position:absolute;right:30px;top:-55px;width:220px;height:220px;border:28px solid #ffffff0b;border-radius:40px;transform:rotate(32deg);pointer-events:none}
.workspace-banner:before{content:'';position:absolute;right:100px;bottom:-150px;width:330px;height:230px;border:1px solid #e8c78a60;border-radius:50%;transform:rotate(-25deg);pointer-events:none}
.banner-tag{display:inline-block;background:#ffffff12;border:1px solid #ffffff22;border-radius:30px;padding:5px 12px;color:#f3d59a}
[data-testid="stColumn"]:nth-child(2) [data-testid="stMetric"]{background:linear-gradient(130deg,#fff,#ebeffb)!important;border-top-color:#8b9bd3!important}
[data-testid="stColumn"]:nth-child(3) [data-testid="stMetric"]{background:linear-gradient(130deg,#fff,#fff0dd)!important;border-top-color:#d9ad6a!important}
[data-testid="stColumn"]:nth-child(4) [data-testid="stMetric"]{background:linear-gradient(130deg,#fff,#f1eafb)!important;border-top-color:#b499d1!important}
[data-testid="stVerticalBlockBorderWrapper"]{background:#ffffffbb;border-radius:16px;box-shadow:0 8px 25px #30485908}
.stButton button{border-radius:10px;transition:box-shadow .18s,transform .18s}.stButton button:hover{box-shadow:0 5px 15px #1a485c18;transform:translateY(-1px)}
@media(max-width:768px){.workspace-banner{padding:20px}.workspace-banner h2{font-size:24px}.banner-flow{font-size:12px}}
</style>''',unsafe_allow_html=True)
