# 小店补货助手
面向表格记账小店的离线补货演示应用。五个页面串联订单质检、库存配置、解释性补货、双策略回放和数据说明。

## 启动
Windows 双击 `启动助手.bat`；首次使用先安装依赖：
```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.lock.txt
.venv/Scripts/python -m streamlit run app.py
```
原记录使用 Python 3.14。本轮补充 Python 3.11.4 独立环境验收，实际结果见 docs/验收进度.md；锁定文件的 NumPy 改为兼容该环境的 2.4.6。

## 演示
1. 导入页点击 UCI 真实交易样本，确认完整覆盖日期，点击核验。
2. 库存页修改现货、在途、交期和箱规。库存日期须匹配决策日。
3. 清单页检查人工复核原因、展开计算证据，导出待确认清单。
4. 回放页选择商品、日期及交期，运行并下载实验 ZIP。

## 验证
```powershell
.venv/Scripts/python -m pytest -q
```
策略使用前 28 天连续日均值，保护期 L+1，误差缓冲只采用决策日前已完成窗口。至少 20 个误差样本，最多 60 个，nearest-rank Q90。基线缓冲固定为预测保护期需求的 20%。两者使用相同初始库存、交期和箱规；当日下单在 t+L 日开门前到账。

## 当前交付边界
内置为 UCI Online Retail II 正式数据的20商品样本，5556条真实交易，2010年1—6月。库存参数仍为演示值。尚未完成中国真实商户验证、比赛实践说明及评审录屏。应用是可运行的软件演示版本；不自动执行采购。依赖首次安装需联网，安装后内置样本可离线运行。

## 中文使用指南
左侧「使用指南」提供快速上手、字段字典、计算算例和常见问题。导入页按四步操作；库存配置带中文列名；补货证据和回放指标提供中文解释。CSV库存模板保留机器字段名，以便再次导入。

## 重建 UCI 样本
从官方页面下载ZIP到 data/raw/online-retail-ii.zip，然后运行 `.venv/Scripts/python scripts/prepare_uci.py`。来源、CC BY 4.0署名、筛选口径、训练/测试区间和哈希见 DATA_SOURCES.md 与 data/demo/manifest.json。

只读核验官方ZIP、工作簿和样本逐行一致性：`.venv/Scripts/python scripts/verify_uci.py`。

运行一商品固定配置公开数据回放验收：`.venv/Scripts/python scripts/validate_public_sample.py`，结果在 outputs/validation/；它是软件实验，不代表真实小店经营效果。

## 后续交付

新增[参数真实性与多条件验证](docs/参数真实性与多条件验证.md)：侧栏区分演示探索与实际采购准备；未确认实际盘点来源时阻止实际模式给出数量。策略回放支持最多8组库存/交期/箱规组合，保留完整结果及模拟声明。

- [真实商户试用方案](docs/真实商户试用方案.md)：需经营者参与，目前未执行。
- [OPC实践说明草稿](docs/OPC创新实践说明草稿.md)：学生实际贡献需本人核实填写。
- [评审录屏脚本](docs/评审录屏脚本.md)：约4分40秒，尚未录制成片。
- [部署操作说明](docs/部署操作说明.md)：待确认发布后进行云端访问验收。
- [验收进度](docs/验收进度.md)：区分已实测与待完成事项。
