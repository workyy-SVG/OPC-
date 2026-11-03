# 功能布局参考与实现记录
检索日期：2026-11-03。参考功能组织思路，未复制品牌素材或页面代码。

- TEAMNAME OPC https://teamnameopc.com/ ：按业务组织工作台入口。对应本项目经营总览和任务入口。
- Odoo 补货报告 https://www.odoo.com/documentation/17.0/applications/inventory_and_mrp/inventory/warehouses_storage/replenishment/report.html ：将提前期和需求预测集中到补货工作面板。对应本项目优先复核的建议列表与证据卡。
- Zoho 库存报告 https://www.zoho.com/us/inventory/help/reports/inventory-reports.html ：关注库存水平和流动。对应本项目指标总览、库存明细与回放趋势。

流程：经营总览 → 数据核验 → 库存设置 → 补货审核；策略回放作为分析工具独立进入。页面保留下一步按钮；更换数据清空旧结果；回放在本次会话保留，参数变更时提示重新运行。
