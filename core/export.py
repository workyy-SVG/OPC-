import pandas as pd
def csv_bytes(df):
    out=df.copy()
    for col in out:
        out[col]=out[col].map(lambda x: "'"+x if isinstance(x,str) and x.lstrip().startswith(('=','+','-','@','\t','\r')) else x)
    return out.to_csv(index=False).encode('utf-8-sig')
