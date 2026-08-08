"""GEX厚み比の検証(タスク#7 ステップC).

net(|totalGEX|) は符号付き合計の絶対値なので、厚いが均衡した板でゼロ近傍になる。
gross = Σ|netGEX| なら純粋な「板の厚み」。近傍限定版 near(±10%) も比較する。

結果と考察は docs/gex-thickness-ratio-study.md を参照。
データが増えたら再実行して閾値とU字の再現性を確認すること。

    python scripts/analyze_thickness_ratio.py
"""
import json, glob, os, statistics as st
from collections import defaultdict

ROOT = 'data/r2/gex/daily'
dates = sorted(os.listdir(ROOT))
hist = defaultdict(dict)  # sym -> date -> dict of measures

for d in dates:
    for f in glob.glob(f'{ROOT}/{d}/*.json'):
        j = json.load(open(f, encoding='utf-8'))
        tot, spot = j.get('totalGEX'), j.get('spotPrice')
        prof = (j.get('profile') or {}).get('total')
        if tot is None or not spot or not prof:
            continue
        gross = sum(abs(p['netGEX']) for p in prof)
        near = sum(abs(p['netGEX']) for p in prof
                   if abs(p['strike'] / spot - 1) <= 0.10)
        hist[j['ticker']][d] = dict(net=abs(tot), gross=gross, near=near, spot=spot)

syms = sorted(s for s, v in hist.items() if len(v) >= len(dates) * 0.8)
print(f'期間 {dates[0]}〜{dates[-1]} ({len(dates)}日)  常時収録 {len(syms)}銘柄')

def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * p))]

def study(key, N=20):
    rows = []
    for s in syms:
        ds = sorted(hist[s])
        for i in range(N, len(ds)):
            med = st.median([hist[s][x][key] for x in ds[i - N:i]])
            if med <= 0:
                continue
            r = hist[s][ds[i]][key] / med
            nxt = (abs(hist[s][ds[i + 1]]['spot'] / hist[s][ds[i]]['spot'] - 1) * 100
                   if i + 1 < len(ds) else None)
            rows.append((r, nxt))
    vals = [x[0] for x in rows]
    print(f'\n=== {key} (N={N}, n={len(rows)}) ===')
    print('  分位: ' + '  '.join(f'p{int(p*100)}={pct(vals,p):.2f}'
                                for p in (.05, .10, .25, .50, .75, .90, .95)))
    ok = sorted([x for x in rows if x[1] is not None])
    q = len(ok) // 5
    print('  五分位 → 翌日|リターン|:')
    for k in range(5):
        seg = ok[k * q:(k + 1) * q] if k < 4 else ok[4 * q:]
        m = [x[1] for x in seg]
        print(f'    Q{k+1} {seg[0][0]:.2f}〜{seg[-1][0]:.2f}: '
              f'中央値 {st.median(m):.2f}%  平均 {sum(m)/len(m):.2f}%  p90 {pct(m,.9):.2f}%')

for k in ('net', 'gross', 'near'):
    study(k)

# 参考: gross と net のスケール比(どれだけ相殺されているか)
print('\n=== 直近日の net/gross 比(小さいほど板が均衡=netは厚みを表さない) ===')
last = dates[-1]
for s in syms:
    h = hist[s].get(last)
    if h:
        print(f'  {s:6} net={h["net"]/1e9:7.2f}B  gross={h["gross"]/1e9:7.2f}B  '
              f'net/gross={h["net"]/h["gross"]*100:5.1f}%')
