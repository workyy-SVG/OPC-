"""Explicit, reviewable AI actions for existing workflows."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from core.ai import AIError, configured, purchase_draft, match_headers, explain
from core.purchasing import add_purchase, integer, day


def provider_picker(key):
    provider = st.selectbox('AI服务', ['qwen','deepseek'], format_func=lambda p: '千问' if p == 'qwen' else 'DeepSeek', key=key)
    if not configured(provider):
        name = 'DASHSCOPE_API_KEY' if provider == 'qwen' else 'DEEPSEEK_API_KEY'
        st.warning(f'当前应用未读到 {name}，手动功能仍可使用。')
        st.caption('请在本应用 Manage app → Settings → Secrets 中，将对应名称放在文件顶部（任何[分组]之前），保存后重启应用。不要把真实Key发到聊天或截图里。')
    else:
        st.caption('已读取该服务的Key；调用权限与额度以实际请求为准。')
    return provider


def headers_ui(columns):
    with st.expander('智能匹配表头'):
        st.caption('仅发送列名，不发送订单行；应用后请核对四个字段。')
        provider = provider_picker('header_provider')
        use_ai = st.checkbox('陌生列名使用AI识别（会发送列名）')
        if st.button('匹配并填写字段'):
            try:
                matches = match_headers(columns, provider, use_ai)
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
        provider = provider_picker('purchase_ai_provider')
        multiline = st.checkbox('使用多行输入（加载异常时请取消勾选）', key='purchase_multiline')
        widget = st.text_area if multiline else st.text_input
        text = widget('采购描述或单据补充说明', placeholder='例如：商品001，10箱，每箱24件，周五到货', key='purchase_description')
        photo = st.file_uploader('采购单或送货单照片', type=['png','jpg','jpeg'])
        reference = st.date_input('相对日期参考日', value=datetime.now(ZoneInfo('Asia/Shanghai')).date())
        consent = st.checkbox('我同意发送以上描述和图片及候选商品编码到所选AI服务，已移除隐私信息')
        st.caption('照片识别须选择千问并配置视觉模型。送货单仅生成草稿，不会直接入库。')
        scope = (tuple(inventory.sku), str(as_of))
        if st.button('生成采购草稿', disabled=not consent):
            st.session_state.pop('ai_purchase_draft', None)
            try:
                image = (photo.getvalue(), 'image/png' if photo.name.lower().endswith('.png') else 'image/jpeg') if photo else None
                rows = purchase_draft(text, inventory.sku.tolist(), reference, provider, image)
                st.session_state.ai_purchase_draft = dict(scope=scope, rows=rows)
            except AIError as error:
                st.error(str(error))
        draft = st.session_state.get('ai_purchase_draft')
        if draft and draft['scope'] == scope:
            st.warning('以下是AI草稿，请逐行核对商品、件数及日期；缺失字段必须补齐。')
            edited = st.data_editor(pd.DataFrame(draft['rows']), hide_index=True, key='purchase_ai_editor',
                column_config={'sku':st.column_config.SelectboxColumn('商品编码', options=inventory.sku.tolist(), required=True),
                               'qty':st.column_config.NumberColumn('总件数', min_value=1, step=1, required=True),
                               'expected_at':st.column_config.TextColumn('预计到货日期 YYYY-MM-DD'),
                               'note':st.column_config.TextColumn('歧义说明/确认原因')})
            ordered = st.date_input('草稿采购日期', value=as_of.date())
            verified = st.checkbox('我已逐行核对草稿，包括箱规、日期及已有采购，确认登记')
            if st.button('确认草稿并登记采购', disabled=not verified):
                try:
                    ledger = st.session_state.purchases
                    for row in edited.to_dict('records'):
                        if row['sku'] not in set(inventory.sku):
                            raise ValueError('请为每行选择现有商品编码')
                        note = '' if pd.isna(row['note']) else str(row['note']).strip()
                        ledger = add_purchase(ledger, row['sku'], integer(row['qty'], 1), ordered,
                                              day(row['expected_at']), 'AI草稿经人工核对：' + note)
                    persist(ledger)
                    st.session_state.pop('ai_purchase_draft', None)
                    st.success('已登记采购，未执行下单或入库。')
                    st.rerun()
                except (ValueError, TypeError, OSError) as error:
                    st.error('未登记：' + str(error))


def explanation_ui(decisions):
    with st.expander('AI补货解释与问答'):
        provider = provider_picker('explanation_provider')
        sku = st.selectbox('要解释的商品', decisions.sku.tolist())
        question = st.text_input('补货问题', value='为什么给出这个建议？我还需要核对什么？')
        consent = st.checkbox('同意发送此商品计算证据及问题到AI服务')
        # JSON serialization replaces NaN with null and avoids raw order rows.
        evidence = json.loads(decisions[decisions.sku == sku].to_json(orient='records'))[0]
        if st.button('根据证据解释', disabled=not consent):
            st.session_state.pop('ai_explanation', None)
            try:
                answer = explain(question, evidence, provider)
                st.session_state.ai_explanation = dict(evidence=evidence, question=question, answer=answer)
            except AIError as error:
                st.error(str(error))
        saved = st.session_state.get('ai_explanation')
        if saved and saved['evidence'] == evidence and saved['question'] == question:
            st.text(saved['answer'])
            st.caption('AI解释仅供辅助，以程序计算证据和人工核对为准。')
