from copy import deepcopy
import json
import pandas as pd
import pytest
from core.ingest import parse_orders
from core.inventory import parse_inventory
from core.numbers import integer, MAX_QUANTITY
from core.purchasing import (add_purchase, register_purchase, effective_stock, receive_into_inventory,
    load_workspace, check_workspace_source, workspace_path)
from core.sales_update import update_stock


def raw():
    return pd.DataFrame([dict(order_id='CUST-1001',sku=' 001 ',order_at='2025-04-01',qty='2')])


def inv():
    return pd.DataFrame([dict(sku='001',as_of='2025-04-01',on_hand=20,on_order=100,lead_days=3,
        pack_size=1,min_order=0,arrival_confirmed=True,source='盘点',parameter_kind='实际盘点',verified=True)])


def test_generic_and_uci_cancellation_are_separate():
    clean,_,summary=parse_orders(raw())
    assert len(clean)==1 and clean.iloc[0].sku=='001'
    assert parse_orders(raw(),source='uci')[2]['cancelled']==1
    assert parse_orders(raw().assign(订单状态=' 已取消 '))[2]['cancelled']==1
    assert parse_orders(raw().assign(订单状态='已完成'))[2]['valid']==1


def test_empty_line_ids_cannot_hide_duplicates_and_diagnostic_fields_do_not_crash():
    frame=pd.concat([raw(),raw()],ignore_index=True).assign(line_id=' ')
    clean,issues,summary=parse_orders(frame)
    assert summary['suspicious']==2 and len(clean)==2
    _,issues,summary=parse_orders(raw().assign(qty='bad',reason_code='old',row_no='old-row',hint='old-hint'))
    assert summary['error']==1
    assert issues.iloc[0].reason_code=='INVALID_FIELD'
    assert issues.iloc[0].original_reason_code=='old'
    assert issues.iloc[0].original_row_no=='old-row'


@pytest.mark.parametrize('value',[1e30,'9007199254740993','1.5','inf',True,None])
def test_bad_quantities_rejected_everywhere(value):
    with pytest.raises(ValueError): integer(value)
    frame=inv().astype(object);frame.loc[0,'on_hand']=value
    with pytest.raises(ValueError): parse_inventory(frame,frame.columns)


def test_external_pipeline_is_not_decremented_for_managed_receipts():
    inventory=inv();stock=inventory.iloc[0].to_dict()
    ledger,updated=register_purchase([],inventory,'001',10,'2025-04-01','2025-04-04','确认','request-1')
    assert effective_stock(stock,ledger,'2025-04-01')['on_order']==110
    ledger,received,moves=receive_into_inventory(ledger,updated,[],ledger[0]['id'],4,'2025-04-01','receipt-1')
    assert received.iloc[0].on_hand==24 and received.iloc[0].on_order==100
    assert effective_stock(received.iloc[0].to_dict(),ledger,'2025-04-01')['on_order']==106
    same,unchanged,again=receive_into_inventory(ledger,received,moves,ledger[0]['id'],4,'2025-04-01','receipt-1')
    assert unchanged.iloc[0].on_hand==24 and len(again)==1


def test_external_detail_conversion_and_purchase_retry_are_idempotent():
    ledger,inventory=register_purchase([],inv(),'001',10,'2025-04-01','2025-04-04','外部明细','request-1',True)
    assert inventory.iloc[0].on_order==90
    assert effective_stock(inventory.iloc[0].to_dict(),ledger,'2025-04-01')['on_order']==100
    same,unchanged=register_purchase(ledger,inventory,'001',10,'2025-04-01','2025-04-04','外部明细','request-1',True)
    assert len(same)==1 and unchanged.iloc[0].on_order==90
    with pytest.raises(ValueError): register_purchase(ledger,inventory,'001',11,'2025-04-01','2025-04-04','外部明细','request-1',True)
    with pytest.raises(ValueError): register_purchase([],inv(),'001',101,'2025-04-01','2025-04-04','外部明细','request-2',True)


def test_postdated_and_overdue_orders_have_explicit_effects():
    inventory=inv().iloc[0].to_dict();inventory['on_order']=0
    future=add_purchase([],'001',10,'2025-04-02','2025-04-05','未来采购')
    assert effective_stock(inventory,future,'2025-04-01')['on_order']==0
    overdue=add_purchase([],'001',10,'2025-03-30','2025-03-31','逾期')
    assert not effective_stock(inventory,overdue,'2025-04-01')['arrival_confirmed']


def test_unknown_backup_sku_and_source_mismatch_rejected():
    payload=dict(schema=2,purchases=add_purchase([],'UNKNOWN',10,'2025-04-01','2025-04-02','确认'),inventory=inv().to_dict('records'),movements=[])
    with pytest.raises(ValueError,match='采购商品'): load_workspace(json.dumps(payload))
    with pytest.raises(ValueError): check_workspace_source({'source_hash':'other'},'current')
    with pytest.raises(ValueError): check_workspace_source({},'current')
    check_workspace_source({},'current',True)
    with pytest.raises(ValueError): workspace_path('../outside')


def daily(end):
    return pd.DataFrame(dict(sku='001',date=pd.date_range('2025-03-01',end),sold_qty=2,coverage_status='confirmed'))


def test_sales_snapshot_update_is_incremental_and_revisions_are_rejected():
    previous=daily('2025-03-31');current=daily('2025-04-01')
    inventory=inv()
    updated,changes=update_stock(previous,current,inventory)
    assert updated.iloc[0].on_hand==18 and updated.iloc[0].as_of=='2025-04-02'
    assert inventory.iloc[0].on_hand==20 and changes[0]['sold']==2
    repeated,again=update_stock(current,current,updated)
    assert repeated.iloc[0].on_hand==18 and again==[]
    changed=current.copy();changed.loc[0,'sold_qty']=3
    with pytest.raises(ValueError,match='历史销售'):update_stock(current,changed,updated)
    with pytest.raises(ValueError):update_stock(current,current[current.date>='2025-04-01'],updated)
    insufficient=inventory.copy();insufficient['on_hand']=1
    with pytest.raises(ValueError,match='超过现货'):update_stock(previous,current,insufficient)


def click(app,label):
    next(b for b in app.button if b.label==label).click().run(timeout=60)
    assert not app.exception


def test_ui_purchase_and_list_share_pipeline_and_double_click_is_safe():
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=60).run()
    click(app,'用示例立即体验')
    app.radio(key='navigation').set_value('采购与到货').run()
    next(w for w in app.selectbox if w.label=='采购商品').set_value('85123A').run()
    qty=next(w for w in app.number_input if w.label=='确认采购数量（件）').value
    next(w for w in app.text_input if w.label=='确认或修改原因').set_value('确认')
    next(w for w in app.checkbox if w.label.startswith('我已核对库存')).check()
    click(app,'登记确认采购')
    assert len(app.session_state.purchases)==1
    assert not app.checkbox(key='purchase_ack').value
    assert any(m.value=='当前规则建议：0件' for m in app.markdown)
    click(app,'登记确认采购')
    assert len(app.session_state.purchases)==1
    app.radio(key='navigation').set_value('补货清单').run()
    assert not app.exception
    # The decision evidence in this page now includes the recorded purchase.
    matches=[d.value for d in app.get('json') if '85123A' in str(d.value)]
    from core.policy import recommend
    stock=app.session_state.inventory.set_index('sku').loc['85123A'].to_dict()|{'sku':'85123A'}
    decision=recommend(app.session_state.daily[app.session_state.daily.sku=='85123A'],effective_stock(stock,app.session_state.purchases,stock['as_of']),{},stock['as_of'])
    assert decision['qty']==0 and decision['inventory_position']==qty+int(stock['on_hand'])


def test_ui_invalid_stock_date_is_not_saved():
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=60).run()
    click(app,'用示例立即体验')
    before=app.session_state.inventory.copy(deep=True)
    app.radio(key='navigation').set_value('库存配置').run()
    next(w for w in app.text_input if w.label=='库存快照日期（年-月-日）').set_value('not-a-date')
    click(app,'保存该商品配置')
    pd.testing.assert_frame_equal(before,app.session_state.inventory)
    assert any('未保存' in item.value for item in app.error)
    app.radio(key='navigation').set_value('采购与到货').run()
    assert not app.exception


def test_ai_draft_cannot_be_confirmed_after_source_text_changes(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    monkeypatch.setattr('ai_ui.configured',lambda p:True)
    monkeypatch.setattr('ai_ui.purchase_draft',lambda text,skus,*args,**kwargs:[dict(sku=skus[0],qty=12,expected_at='2010-07-04',note='确认')])
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=60).run()
    click(app,'用示例立即体验')
    app.radio(key='navigation').set_value('采购与到货').run()
    app.text_input(key='purchase_description').set_value('订12件')
    next(w for w in app.checkbox if w.label.startswith('我同意发送')).check().run()
    click(app,'生成采购草稿')
    assert any(b.label=='确认草稿并登记采购' for b in app.button)
    app.text_input(key='purchase_description').set_value('改为订24件').run()
    assert not any(b.label=='确认草稿并登记采购' for b in app.button)
    assert not app.session_state.purchases


def test_workspace_concurrent_sessions_do_not_overwrite(tmp_path):
    from core.purchasing import save_workspace
    path=tmp_path/'workspace.json'
    first=save_workspace(path,[],inv(),[],expected_revision=0)
    assert first==1
    second=save_workspace(path,[],inv(),[],expected_revision=first)
    assert second==2
    before=path.read_bytes()
    with pytest.raises(ValueError,match='其他会话更新'):
        save_workspace(path,[],inv(),[],expected_revision=first)
    assert path.read_bytes()==before
    assert not list(tmp_path.glob('*.lock'))


def test_ui_sales_update_preserves_orders_and_deducts_only_new_sales():
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=60).run()
    app.radio(key='navigation').set_value('导入与质检').run()
    initial=pd.DataFrame(dict(order_id=[str(i) for i in range(90)],sku='001',
        order_at=pd.date_range('2025-01-01',periods=90).strftime('%Y-%m-%d'),qty=1))
    upload=next(w for w in app.get('file_uploader') if w.label=='上传销售 CSV')
    upload.upload('initial.csv',initial.to_csv(index=False).encode()).run()
    app.checkbox(key='sales_coverage_confirmed').check().run()
    click(app,'核验并使用此数据')
    app.radio(key='navigation').set_value('采购与到货').run()
    next(w for w in app.number_input if w.label=='确认采购数量（件）').set_value(3)
    app.text_input(key='purchase_reason').set_value('真实表格测试')
    app.checkbox(key='purchase_ack').check()
    click(app,'登记确认采购')
    purchase_id=app.session_state.purchases[0]['id']
    before=int(app.session_state.inventory.iloc[0].on_hand)
    app.radio(key='navigation').set_value('导入与质检').run()
    app.toggle(key='open_sales_import').set_value(True).run()
    current=pd.concat([initial,pd.DataFrame([dict(order_id='new',sku='001',order_at='2025-04-01',qty=2)])])
    upload=next(w for w in app.get('file_uploader') if w.label=='上传销售 CSV')
    upload.upload('current.csv',current.to_csv(index=False).encode()).run()
    app.checkbox(key='sales_coverage_confirmed').check().run()
    click(app,'核验并使用此数据')
    assert app.session_state.purchases[0]['id']==purchase_id
    assert int(app.session_state.inventory.iloc[0].on_hand)==before-2
    assert app.session_state.inventory.iloc[0].as_of=='2025-04-02'
    app.toggle(key='open_sales_import').set_value(True).run()
    upload=next(w for w in app.get('file_uploader') if w.label=='上传销售 CSV')
    upload.upload('again.csv',current.to_csv(index=False).encode()).run()
    app.checkbox(key='sales_coverage_confirmed').check().run()
    click(app,'核验并使用此数据')
    assert int(app.session_state.inventory.iloc[0].on_hand)==before-2
    assert len(app.session_state.sales_updates)==1


def test_ui_recover_workspace_restores_orders_and_inventory():
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    source=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=60).run()
    click(source,'用示例立即体验')
    source.radio(key='navigation').set_value('采购与到货').run()
    source.text_input(key='purchase_reason').set_value('恢复测试')
    source.checkbox(key='purchase_ack').check()
    click(source,'登记确认采购')
    code=source.session_state.purchase_workspace
    purchase_id=source.session_state.purchases[0]['id']
    target=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=60).run()
    click(target,'用示例立即体验')
    target.radio(key='navigation').set_value('采购与到货').run()
    next(w for w in target.text_input if w.label=='已有工作区恢复码').set_value(code).run()
    click(target,'恢复工作区')
    assert target.session_state.purchases[0]['id']==purchase_id
    pd.testing.assert_frame_equal(source.session_state.inventory,target.session_state.inventory)


def test_reconciled_history_revision_requires_verified_actual_stock():
    previous=daily('2025-03-31');current=daily('2025-04-01')
    current.loc[0,'sold_qty']=3
    inventory=inv()
    updated,changes=update_stock(previous,current,inventory,allow_revision=True)
    assert updated.iloc[0].on_hand==18
    assert changes[0]['sold']==0 and '历史修订' in changes[0]['note']
    inventory['verified']=False
    with pytest.raises(ValueError,match='实际盘点'):
        update_stock(previous,current,inventory,allow_revision=True)


def test_optional_status_column_conflicts_are_not_silent():
    with pytest.raises(ValueError,match='内容冲突'):
        parse_orders(raw().assign(status='completed',订单状态='取消'))
    assert parse_orders(raw().assign(status='completed',订单状态='completed'))[2]['valid']==1
    with pytest.raises(ValueError): check_workspace_source([], 'current')


def test_replay_rejects_unknown_coverage_fractional_sales_and_invalid_parameters():
    from core.replay import run_replay
    from core.policy import recommend
    h=pd.DataFrame(dict(sku='001',date=pd.date_range('2025-01-01',periods=100),sold_qty=2,coverage_status='confirmed'))
    cfg=dict(start='2025-04-01',end='2025-04-03',initial_stock=20,lead_days=3)
    with pytest.raises(ValueError):run_replay(h.assign(sold_qty=2.5),cfg)
    with pytest.raises(ValueError):run_replay(h.assign(coverage_status='unknown'),cfg)
    stock=inv().iloc[0].to_dict()
    with pytest.raises(ValueError,match='历史日期重复'):recommend(pd.concat([h,h.iloc[:1]]),stock,{},'2025-04-01')
    with pytest.raises(ValueError,match='缓冲比例'):recommend(h,stock,{'adaptive_scale':float('inf')},'2025-04-01')
    assert recommend(h,stock,{'policy':'baseline','baseline_ratio':'0.2'},'2025-04-01')['qty'] is not None
    from core.validation import parameter_status
    assert not parameter_status(stock|{'source':None})


def test_managed_and_external_pipeline_total_cannot_overflow():
    inventory=inv();inventory['on_order']=MAX_QUANTITY
    with pytest.raises(ValueError):
        register_purchase([],inventory,'001',1,'2025-04-01','2025-04-04','超上限','id1')
    ledger,converted=register_purchase([],inventory,'001',1,'2025-04-01','2025-04-04','转明细','id2',True)
    assert effective_stock(converted.iloc[0].to_dict(),ledger,'2025-04-01')['on_order']==MAX_QUANTITY


@pytest.mark.parametrize('updates',['bad',[None],[{'sku':'001','start':'2025-04-01','end':'2025-04-01','sold':2,'before':10,'after':9}]])
def test_backup_sales_updates_are_validated(updates):
    payload=dict(schema=2,purchases=[],inventory=inv().to_dict('records'),movements=[],sales_updates=updates)
    with pytest.raises(ValueError):load_workspace(json.dumps(payload))


def test_old_session_requires_explicit_pipeline_reconciliation():
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    app=AppTest.from_file(Path(__file__).resolve().parents[1]/'app.py',default_timeout=60).run()
    click(app,'用示例立即体验')
    code=str(app.session_state.inventory.iloc[0].sku)
    app.session_state.purchases=add_purchase([],code,1,'2010-07-01','2010-07-04','旧记录')
    app.radio(key='navigation').set_value('采购与到货').run()
    assert not app.exception
    assert any('升级前采购记录' in item.value for item in app.warning)
    assert not any(b.label=='登记确认采购' for b in app.button)
    next(w for w in app.checkbox if w.label.startswith('我已核对所有商品')).check().run()
    click(app,'确认升级在途口径')
    assert any(b.label=='登记确认采购' for b in app.button)
