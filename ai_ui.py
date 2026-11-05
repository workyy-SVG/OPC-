"""Explicit, reviewable AI actions for existing workflows."""
import json
import hashlib
from uuid import uuid4
import pandas as pd
import streamlit as st

from core.ai import AIError, configured, purchase_draft, match_headers, explain, complete
from core.purchasing import add_purchase, integer, day


ONBOARDING_REVISION = 1


def provider_picker(key):
    provider = st.selectbox('AI 服务', ['qwen','deepseek'], format_func=lambda p: '千问' if p == 'qwen' else 'DeepSeek', key=key,
                            help='两种 AI 服务共用每日调用额度。识别照片时选千问。')
    available = configured(provider)
    st.caption('已读取Key，接口连接、权限和额度需实际验证。' if available else '所选 AI 服务暂不可用，仍可手动填写。')
    if st.button('测试AI连接（计1次）', key=key+'_connection', disabled=not available):
        try:
            result=complete('只返回JSON对象 {"ok":true}。', {'connection_test':True},provider)
            if result.get('ok') is not True: raise AIError('接口已响应，但连接测试结果格式不符')
            st.success('文字接口连接成功。视觉模型权限仍需图片调用验证。')
        except AIError as error:
            st.error(str(error))
    return provider, available


def headers_ui(columns):
    with st.expander('智能匹配表头'):
        st.caption('先识别常见列名。勾选 AI 后，只向所选服务发送列名来匹配陌生字段。填入后请核对四项映射。')
        provider, available = provider_picker('header_provider')
        use_ai = st.checkbox('陌生列名使用AI识别（会发送列名）', disabled=not available)
        if st.button('匹配并填写字段'):
            try:
                matches = match_headers(columns, provider, use_ai and available)
                for field, column in matches.items():
                    st.session_state['map_' + field] = column
                if len(matches) < 4:
                    st.warning('部分字段无法确定，请继续手动选择。')
                else:
                    st.success('已填写匹配结果，请核对后再核验数据。')
            except AIError as error:
                st.error(str(error))


def purchasing_ai(inventory, as_of, persist):
    with st.expander('AI录采购：文字或单据照片'):
        st.caption('把描述或单据转换为采购草稿。请核对总件数与预计到货日期，确认后才会登记到采购台账。')
        provider, available = provider_picker('purchase_ai_provider')
        multiline = st.checkbox('使用多行输入（加载异常时请取消勾选）', key='purchase_multiline')
        widget = st.text_area if multiline else st.text_input
        text = widget('采购描述或单据补充说明', placeholder='例如：商品001，10箱，每箱24件，周五到货', key='purchase_description')
        photo = st.file_uploader('采购单或送货单照片', type=['png','jpg','jpeg'])
        reference = st.date_input('相对日期参考日', value=as_of.date(), key=f'purchase_reference_{as_of.date()}',
                                  help='默认和本次库存决策日期一致。「周五」「明天」按此日期理解。')
        st.caption('照片识别使用千问。识别出的内容会先显示为草稿，供你检查。')
        consent = st.checkbox('我同意发送以上描述和图片及候选商品名称与编码到所选AI服务，已移除隐私信息')
        photo_hash=hashlib.sha256(photo.getvalue()).hexdigest() if photo else None
        names=st.session_state.get('product_names',{})
        scope=hashlib.sha256(json.dumps([inventory.sku.tolist(),str(as_of),text,photo_hash,provider,str(reference),names,
            st.session_state.get('sales_signature')],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if st.button('生成采购草稿', disabled=not available or not consent,
                     help='确认上方数据发送选项后即可生成草稿。'):
            st.session_state.pop('ai_purchase_draft', None)
            try:
                image = (photo.getvalue(), 'image/png' if photo.name.lower().endswith('.png') else 'image/jpeg') if photo else None
                rows = purchase_draft(text, inventory.sku.tolist(), reference, provider, image, product_names=names)
                st.session_state.ai_purchase_draft = dict(scope=scope, rows=rows, id=uuid4().hex)
            except AIError as error:
                st.error(str(error))
        draft = st.session_state.get('ai_purchase_draft')
        if draft and draft['scope'] != scope:
            st.warning('输入、图片、参考日或服务已变化，旧草稿不再可登记，请重新生成。')
        if draft and draft['scope'] == scope:
            st.warning('以下是AI草稿，请逐行核对商品、件数及日期；缺失字段必须补齐。')
            edited = st.data_editor(pd.DataFrame(draft['rows']), hide_index=True, key='purchase_ai_editor_'+draft['id'],
                column_config={'sku':st.column_config.SelectboxColumn('商品编码', options=inventory.sku.tolist(), required=True),
                               'qty':st.column_config.NumberColumn('总件数', min_value=1, step=1, required=True),
                               'expected_at':st.column_config.TextColumn('预计到货日期 YYYY-MM-DD'),
                               'note':st.column_config.TextColumn('歧义说明/确认原因')})
            ordered = st.date_input('草稿采购日期', value=as_of.date())
            verified = st.checkbox('我已逐行核对草稿，包括每箱件数、日期及已有采购，确认登记', key='draft_ack_'+draft['id'])
            if st.button('确认草稿并登记采购', disabled=not verified):
                try:
                    ledger = st.session_state.purchases
                    for index,row in enumerate(edited.to_dict('records')):
                        if row['sku'] not in set(inventory.sku):
                            raise ValueError('请为每行选择现有商品编码')
                        note = '' if pd.isna(row['note']) else str(row['note']).strip()
                        ledger = add_purchase(ledger, row['sku'], integer(row['qty'], 1), ordered,
                                              day(row['expected_at']), 'AI草稿经人工核对：' + note, request_id=draft['id']+'-'+str(index))
                    persist(ledger)
                    st.session_state.pop('ai_purchase_draft', None)
                    st.success('已登记采购，未执行下单或入库。')
                    st.rerun()
                except (ValueError, TypeError, OSError) as error:
                    st.error('未登记：' + str(error))


def explanation_ui(decisions, product_label=str):
    with st.expander('AI补货解释与问答'):
        st.caption('用自然语言读懂某个商品的计算证据，也可询问缺项和核对方法。程序计算的建议数量以清单为准。')
        provider, available = provider_picker('explanation_provider')
        sku = st.selectbox('要解释的商品', decisions.sku.tolist(), format_func=product_label)
        question = st.text_input('补货问题', value='为什么给出这个建议？我还需要核对什么？')
        consent = st.checkbox('同意发送此商品计算证据及问题到AI服务')
        # JSON serialization replaces NaN with null and avoids raw order rows.
        evidence = json.loads(decisions[decisions.sku == sku].to_json(orient='records'))[0]
        if st.button('根据证据解释', disabled=not available or not consent,
                     help='选择商品、填写问题并勾选上方同意项后，即可发送给 AI。'):
            st.session_state.pop('ai_explanation', None)
            try:
                answer = explain(question, evidence, provider)
                st.session_state.ai_explanation = dict(evidence=evidence, question=question, answer=answer, provider=provider)
            except AIError as error:
                st.error(str(error))
        saved = st.session_state.get('ai_explanation')
        if saved and saved['evidence'] == evidence and saved['question'] == question and saved.get('provider')==provider:
            st.text(saved['answer'])
            st.caption('AI解释仅供辅助，以程序计算证据和人工核对为准。')

APP_REVISION = "audit-fixes-20261104"
