"""Read-only verification of the bundled sample against the official archive."""
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def verify():
    manifest = json.loads((ROOT / 'data/demo/manifest.json').read_text(encoding='utf-8'))
    archive = (ROOT / 'data/raw/online-retail-ii.zip').read_bytes()
    assert hashlib.sha256(archive).hexdigest() == manifest['zip_sha256'], '官方ZIP哈希不一致'
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        workbook = z.read(next(n for n in z.namelist() if n.endswith('.xlsx')))
    assert hashlib.sha256(workbook).hexdigest() == manifest['xlsx_sha256'], '工作簿哈希不一致'
    raw = pd.read_excel(io.BytesIO(workbook), sheet_name=0, dtype={'Invoice': str, 'StockCode': str})
    raw = raw.rename(columns={'Invoice': 'order_id', 'StockCode': 'sku', 'InvoiceDate': 'order_at', 'Quantity': 'qty', 'Price': 'unit_price'})
    raw['order_at'] = pd.to_datetime(raw.order_at)
    train = raw[(raw.order_at >= '2010-01-01') & (raw.order_at < '2010-04-01') & (raw.qty > 0) & ~raw.order_id.str.upper().str.startswith('C') & raw.sku.str.fullmatch(r'\d{5}[A-Za-z]?')]
    selected = train.groupby('sku').qty.sum().sort_values(ascending=False, kind='stable').head(20).index.tolist()
    assert selected == manifest['selected_skus'], '训练期选品不一致'
    expected = raw[raw.sku.isin(selected) & (raw.order_at >= '2010-01-01') & (raw.order_at < '2010-07-01')][['order_id', 'sku', 'order_at', 'qty', 'unit_price']].copy()
    expected['order_at'] = expected.order_at.dt.strftime('%Y-%m-%d')
    sample_path = ROOT / 'data/demo/orders.csv'
    assert hashlib.sha256(sample_path.read_bytes()).hexdigest() == manifest['sample_sha256'], '样本文件哈希不一致'
    actual = pd.read_csv(sample_path, dtype={'order_id': str, 'sku': str})
    pd.testing.assert_frame_equal(expected.reset_index(drop=True), actual, check_dtype=False)
    print(json.dumps({'verified': True, 'rows': len(actual), 'skus': actual.sku.nunique(), 'original_rows': len(raw), 'zip_sha256': manifest['zip_sha256'], 'sample_sha256': manifest['sample_sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    verify()
