"""Generate a reproducible software-validation report, not merchant outcomes."""
import json
import sys
import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.ingest import parse_orders
from core.demand import build_daily
from core.replay import run_replay


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sku', help='Optional training-selected SKU; do not tune by test performance')
    parser.add_argument('--matrix', action='store_true', help='Compare a fixed 2x2x2 scenario matrix')
    args = parser.parse_args()
    raw = pd.read_csv(ROOT / 'data/demo/orders.csv', dtype=str)
    clean, issues, summary = parse_orders(raw)
    daily = build_daily(clean, pd.date_range('2010-01-01', '2010-06-30'))
    # Fixed training-period-selected SKU; do not select by test-period performance.
    manifest = json.loads((ROOT / 'data/demo/manifest.json').read_text(encoding='utf-8'))
    sku = args.sku or manifest['selected_skus'][0]
    if sku not in manifest['selected_skus']:
        raise ValueError('SKU必须属于训练期预选商品')
    config = dict(start='2010-05-01', end='2010-06-30', lead_days=3, initial_stock=100, pack_size=6, min_order=0)
    timeline, metrics = run_replay(daily[daily.sku == sku], config)
    assert len(timeline) == 122 and len(metrics) == 2
    assert timeline[['demand', 'ending_stock', 'shortage', 'order']].ge(0).all().all()
    total_demand = float(timeline[timeline.policy == 'baseline'].demand.sum())
    if total_demand:
        assert metrics.fill_rate.between(0, 1).all()
    else:
        assert metrics.fill_rate.isna().all(), '零需求期不应生成满足率'
    output = ROOT / 'outputs/validation' / sku
    output.mkdir(parents=True, exist_ok=True)
    safe_metrics = metrics.astype(object).where(pd.notna(metrics), None)
    report = dict(dataset=manifest['dataset'], sample_sha256=manifest['sample_sha256'], sample_rows=len(raw), clean_rows=len(clean), issue_rows=len(issues), summary=summary, sku=sku, config=config, total_test_demand=total_demand, coverage_assumption=manifest['coverage_assumption'], metrics=safe_metrics.to_dict(orient='records'), limitations=['Only one preselected SKU tested here; no claim of broad strategy superiority', 'Zero-demand test periods have no defined fill rate', 'Inventory and procurement settings are simulated', 'Historical sales proxy does not include unobserved lost sales', 'No real merchant field study completed'])
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    timeline.to_csv(output / 'timeline.csv', index=False)
    metrics.to_csv(output / 'metrics.csv', index=False)
    if args.matrix:
        from core.validation import sensitivity, compare_scenarios
        scenarios=sensitivity(daily[daily.sku==sku],config,[20,100],[2,7],[1,6])
        scenarios.to_csv(output/'scenario_metrics.csv',index=False)
        compare_scenarios(scenarios).to_csv(output/'scenario_comparisons.csv',index=False)
        (output/'scenario_manifest.json').write_text(json.dumps(dict(sku=sku,config=config,initial_stocks=[20,100],lead_days=[2,7],pack_sizes=[1,6],data_hash=manifest['sample_sha256'],evidence_type='Simulated sensitivity experiment; not merchant-observed outcomes'),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
