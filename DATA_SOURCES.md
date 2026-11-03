## 演示数据说明
当前内置 orders.csv 为固定种子 42 生成的合成订单，20 个 SKU，2025-01-01 至 2025-05-30，共 3000 行。不是 UCI 数据，也不代表真实商户。
库存默认值是合成演示参数，使用前须填写实际库存并确认按期在途。
公开数据适配目标：[UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii)。正式公开数据实验仍需下载原始数据、核验许可并保存处理记录。当前结果仅用于软件验收。
已排除非商品编码：POST、D、M、C2、DOT、BANK CHARGES。取消与退货不冲减需求。未知覆盖不补零。
