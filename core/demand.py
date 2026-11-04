import pandas as pd
import duckdb
def build_daily(clean_rows, coverage):
    if clean_rows.empty: return pd.DataFrame(columns=['sku','date','sold_qty','coverage_status'])
    with duckdb.connect(':memory:') as con:
        con.register('orders',clean_rows)
        totals=con.execute('SELECT sku, CAST(order_at AS DATE) AS date, SUM(qty) AS sold_qty FROM orders GROUP BY ALL').df()
    rows=[]; confirmed=set(pd.to_datetime(coverage))
    for sku,g in totals.groupby('sku'):
        dates=pd.date_range(min(confirmed | set(g.date)),max(confirmed | set(g.date)))
        s=g.set_index('date').sold_qty
        for d in dates: rows.append((sku,d,float(s.get(d,0)) if d in confirmed else float('nan'),'confirmed' if d in confirmed else 'unknown'))
    return pd.DataFrame(rows,columns=['sku','date','sold_qty','coverage_status'])
