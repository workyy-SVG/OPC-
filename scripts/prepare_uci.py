"""Rebuild the UCI sample from the official ZIP; no synthetic sales are generated."""
from pathlib import Path
import zipfile,hashlib,json,sys
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
archive=ROOT/'data/raw/online-retail-ii.zip'
with zipfile.ZipFile(archive) as z:
    name=next(n for n in z.namelist() if n.endswith('.xlsx'))
    workbook=z.read(name)
from io import BytesIO
raw=pd.read_excel(BytesIO(workbook),sheet_name=0,dtype={'Invoice':str,'StockCode':str})
raw=raw.rename(columns={'Invoice':'order_id','StockCode':'sku','InvoiceDate':'order_at','Quantity':'qty','Price':'unit_price'})
raw['order_at']=pd.to_datetime(raw.order_at)
start,end='2010-01-01','2010-06-30'
train=raw[(raw.order_at>='2010-01-01')&(raw.order_at<'2010-04-01')&(raw.qty>0)&~raw.order_id.str.upper().str.startswith('C')&raw.sku.str.fullmatch(r'\d{5}[A-Za-z]?')]
selected=train.groupby('sku').qty.sum().sort_values(ascending=False,kind='stable').head(20).index.tolist()
sample=raw[raw.sku.isin(selected)&(raw.order_at>=start)&(raw.order_at<'2010-07-01')][['order_id','sku','order_at','qty','unit_price']].copy()
# There is no stable transaction-line ID in UCI. Do not invent one.
sample['order_at']=sample.order_at.dt.strftime('%Y-%m-%d')
sample.to_csv(ROOT/'data/demo/orders.csv',index=False)
meta=dict(dataset='UCI Online Retail II',creator='Daqing Chen',citation='Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository.',doi='https://doi.org/10.24432/C5CG6D',source='https://archive.ics.uci.edu/dataset/502/online+retail+ii',license='CC BY 4.0',download_date='2026-11-03',sheet='Year 2009-2010',start=start,end=end,training=['2010-01-01','2010-03-31'],calibration=['2010-04-01','2010-04-30'],test=['2010-05-01','2010-06-30'],sku_count=len(selected),rows=len(sample),selected_skus=selected,selection='Top 20 by positive non-cancelled training-period units; five-digit product codes with optional letter suffix',removed_fields=['Customer ID','Country','Description'],transforms='Date reduced to calendar day; cancellations and negative quantities retained for quality checks; no line IDs invented; no sales generated',coverage_assumption='Full published transaction extract treated as complete calendar-day coverage, including zero-sale days; not independently verified store opening records',zip_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),xlsx_sha256=hashlib.sha256(workbook).hexdigest(),sample_sha256=hashlib.sha256((ROOT/'data/demo/orders.csv').read_bytes()).hexdigest())
(ROOT/'data/demo/manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'rows':len(sample),'skus':len(selected),'sha256':meta['sample_sha256']}))
