import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from core.ingest import parse_orders
from core.demand import build_daily
from core.study import run_study
raw=pd.read_csv('data/demo/orders.csv',dtype=str)
c,issues,summary=parse_orders(raw, source="uci")
d=build_daily(c,pd.date_range('2010-01-01','2010-06-30'))
skus=['21212','21977','85099B']
cfg=dict(start='2010-06-01',end='2010-06-30',lead_days=3,initial_stock=20)
r=run_study(d[d.sku.isin(skus)],cfg)
out=Path('experiments/uci_review');out.mkdir(parents=True,exist_ok=True)
for k,v in r.items():
 if isinstance(v,pd.DataFrame):v.to_csv(out/(k+'.csv'),index=False)
(out/'manifest.json').write_text(json.dumps(dict(config=cfg,skus=skus,ci=r['ci'],sample_sha256=json.loads(Path('data/demo/manifest.json').read_text())['sample_sha256'],scope='3 SKU x 30 days smoke experiment, not confirmatory evidence'),indent=2),encoding='utf-8')
print(r['summary'].to_string(index=False))

