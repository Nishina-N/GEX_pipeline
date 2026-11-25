# -*- coding: utf-8 -*-
"""日々の分析の型（5つの面の天気）を計算し、テクニカルの図を描く。

使い方:
  python scripts/daily_framework.py --date 2026-11-20

出力:
  data/framework/{date}.json        各面の数字・天気・見立て（SOUBA から借りた値の写しも含む）
  data/framework/{date}_{SPY,QQQ,SMH}_tech.png 日足（移動平均・価格帯別出来高・出来高）
  data/framework/{date}_macro.png / _sentiment.png / _sector.png 5つの面の図
状態は 健全（+1）・様子見（0）・注意（-1）・警報（-2）。心理の面は警報を使わず「行き過ぎ」を添える。

基準は Obsidian の fleet note/構成案/分析の型_v0.md と同じ（仮の数字）。
Fear & Greed は data/framework/fg_{date}.json（{"score": 9.7, "history": [["2026-01-02", 45], ...]}）があれば使う。
SOUBA の値は as_of が記事の日付と一致するときだけ使う。使えない面は weather=None とし、
記事には「今回は計算が間に合っていません」と書く（日報は止めない）。
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tv_style as T
plt.rcParams["font.family"] = "Yu Gothic"
plt.rcParams["axes.unicode_minus"] = False

OUT_DIR = os.path.join("data", "framework")
LEVELS = os.path.join("data", "r2", "gex", "daily")
SOUBA = r"C:\Users\nishiha\Work\investment\SOUBA_prj2\data\dashboard"
GROWTH = ["XLK", "XLY", "XLC"]
DEFENSIVE = ["XLP", "XLU", "XLV", "XLRE"]
CYCLICAL = ["XLE", "XLF", "XLI", "XLB"]
SECTOR_JA = {"XLK": "情報技術", "XLY": "一般消費財", "XLC": "通信", "XLP": "生活必需品", "XLU": "公益",
             "XLV": "ヘルスケア", "XLRE": "不動産", "XLE": "エネルギー", "XLF": "金融", "XLI": "資本財", "XLB": "素材"}
SCORE = {"健全": 1, "様子見": 0, "注意": -1, "警報": -2}
TECH_SYMS = ["SPY", "QQQ", "SMH"]


def pct(a, b):
    return (a / b - 1) * 100


def face_macro(px, i):
    tnx = px["^TNX"]
    d5 = (tnx.iloc[i] - tnx.iloc[i - 5]) * 100
    spy5 = pct(px["SPY"].iloc[i], px["SPY"].iloc[i - 5])
    if d5 >= 20:
        w, why = "警報", "10年債が5日で20bp以上上昇"
    elif d5 >= 10:
        w, why = "注意", "10年債が5日で10bp以上上昇"
    elif d5 <= -10 and spy5 > 0:
        w, why = "健全", "10年債が低下し、株も上昇"
    elif d5 <= -10:
        w, why = "様子見", "10年債は低下したが株も下落（安全資産への逃避）"
    else:
        w, why = "様子見", "金利の大きな動きなし"
    return dict(weather=w, why=why, tnx=round(float(tnx.iloc[i]), 2), tnx_5d_bp=round(float(d5), 1),
                spy_5d=round(float(spy5), 2),
                dxy=round(float(px["DX-Y.NYB"].iloc[i]), 2), dxy_5d=round(float(pct(px["DX-Y.NYB"].iloc[i], px["DX-Y.NYB"].iloc[i - 5])), 2),
                oil=round(float(px["CL=F"].iloc[i]), 2), oil_5d=round(float(pct(px["CL=F"].iloc[i], px["CL=F"].iloc[i - 5])), 2))


def face_sentiment(px, i, date):
    vix = float(px["^VIX"].iloc[i])
    fg = None
    p = os.path.join(OUT_DIR, f"fg_{date}.json")
    if os.path.exists(p):
        fg = json.load(open(p, encoding="utf-8")).get("score")
    if vix >= 25 or (fg is not None and fg <= 25):
        w = "注意"
    elif vix < 18 and (fg is None or fg >= 45):
        w = "健全"
    else:
        w = "様子見"
    extreme = None
    if (fg is not None and fg <= 15) or vix >= 35:
        extreme = "悲観の行き過ぎ"
    elif fg is not None and fg >= 85:
        extreme = "楽観の行き過ぎ"
    return dict(weather=w, vix=round(vix, 2), vix_5d=round(float(vix - px["^VIX"].iloc[i - 5]), 2),
                fear_greed=fg, fear_greed_missing=fg is None, extreme=extreme)


def face_gex(date):
    if not os.path.exists(os.path.join(LEVELS, date, "SPY.json")):
        return dict(weather=None, missing=f"{date} の GEX データがない")
    out = {}
    for s in ["SPY", "QQQ"]:
        j = json.load(open(os.path.join(LEVELS, date, f"{s}.json"), encoding="utf-8"))
        lv = j["levels"]
        out[s] = dict(spot=j["spotPrice"], hvl=round(lv["hvl"], 2), diff=round(pct(j["spotPrice"], lv["hvl"]), 2),
                      put_wall=lv.get("putWall"), call_wall=lv.get("callWall"),
                      total_gex_bn=round(j.get("totalGEX", 0) / 1e9, 2))
    spy, qqq = out["SPY"]["diff"], out["QQQ"]["diff"]
    if spy > 0.3 and qqq > 0:
        w = "健全"
    elif spy <= -3 and out["SPY"]["put_wall"] and out["SPY"]["spot"] < out["SPY"]["put_wall"]:
        w = "警報"
    elif spy < -0.3:
        w = "注意"
    else:
        w = "様子見"
    return dict(weather=w, **out)


def face_strength(date):
    try:
        mc = json.load(open(os.path.join(SOUBA, "market_condition.json"), encoding="utf-8"))
        mp = json.load(open(os.path.join(SOUBA, "market_pulse.json"), encoding="utf-8"))
    except (OSError, ValueError) as e:
        return dict(weather=None, missing=f"SOUBA のファイルを読めない: {e}")
    if mc.get("as_of") != date or mp.get("as_of") != date:
        return dict(weather=None, missing=f"日付が合わない (market_condition {mc.get('as_of')}, market_pulse {mp.get('as_of')})")
    status, color = mc["status"], mp["map"]["state"]
    above = {c["key"]: c["value"] for c in mc["conditions"] if c["key"].startswith("above_")}
    good_status = status in ("Positive", "Strong Positive")
    bad_status = status in ("Negative", "Strong Negative")
    if status == "Strong Negative" and above.get("above_200", 100) < 30:
        w = "警報"
    elif good_status and color in ("GREEN", "BLUE"):
        w = "健全"
    elif bad_status or color in ("PURPLE", "RED"):
        w = "注意"
    else:
        w = "様子見"
    mcc = mp["map"].get("mcc", {})
    return dict(weather=w, status=status, score=mc["score"], map=color, above_ma_pct=above,
                adv=mcc.get("adv"), dec=mcc.get("dec"), breadth_label=mcc.get("breadth_label"),
                souba_copy=dict(market_condition=mc, market_pulse_map=mp["map"], market_pulse_vmr=mp.get("vmr")))


def face_sector(px, i):
    r5 = {s: pct(px[s].iloc[i], px[s].iloc[i - 5]) for s in GROWTH + DEFENSIVE + CYCLICAL}
    r1 = {s: pct(px[s].iloc[i], px[s].iloc[i - 1]) for s in GROWTH + DEFENSIVE + CYCLICAL}
    g = np.mean([r5[s] for s in GROWTH]); d = np.mean([r5[s] for s in DEFENSIVE]); c = np.mean([r5[s] for s in CYCLICAL])
    if g > 0 and g > d:
        w = "健全"
    elif g <= -5 and d - g >= 3:
        w = "警報"
    elif g < 0 and g < d:
        w = "注意"
    else:
        w = "様子見"
    rank = sorted(r1, key=r1.get, reverse=True)
    fmt = lambda s: dict(symbol=s, name=SECTOR_JA[s], d1=round(float(r1[s]), 2), d5=round(float(r5[s]), 2))
    return dict(weather=w, growth_5d=round(float(g), 2), defensive_5d=round(float(d), 2), cyclical_5d=round(float(c), 2),
                top_1d=[fmt(s) for s in rank[:3]], bottom_1d=[fmt(s) for s in rank[-3:]])


def technical(sym, date, out_png):
    full = yf.download(sym, start=pd.Timestamp(date) - pd.Timedelta(days=420), end=pd.Timestamp(date) + pd.Timedelta(days=1),
                       progress=False, auto_adjust=False)
    full.columns = [c[0] if isinstance(c, tuple) else c for c in full.columns]
    full = full.dropna(subset=["Close"])
    c = full.Close
    ma = {"10 EMA": (c.ewm(span=10).mean(), T.EMA10), "21 EMA": (c.ewm(span=21).mean(), T.EMA21),
          "50 SMA": (c.rolling(50).mean(), T.SMA50), "200 SMA": (c.rolling(200).mean(), T.SMA200)}
    d = full.iloc[-70:]
    rr = full.iloc[-40:]  # 価格帯別出来高は直近40営業日
    lo, hi = rr.Low.min(), rr.High.max()
    bins = np.linspace(lo, hi, 41); vp = np.zeros(40)
    for _, q in rr.iterrows():
        m = (bins[1:] >= q.Low) & (bins[:-1] <= q.High); vp[m] += q.Volume / max(m.sum(), 1)
    order = np.argsort(vp)[::-1]; cum = 0; va = []
    for k in order:
        va.append(k); cum += vp[k]
        if cum >= .7 * vp.sum():
            break
    poc = int(np.argmax(vp)); mid = (bins[:-1] + bins[1:]) / 2
    vah, val = float(bins[max(va) + 1]), float(bins[min(va)])

    x = np.arange(len(d)); dates = [t.strftime("%m/%d") for t in d.index]
    fig = plt.figure(figsize=(12, 7), dpi=120)
    gs = fig.add_gridspec(2, 2, height_ratios=[3, 1], width_ratios=[5, 1], wspace=0.02, hspace=0.08)
    ax = fig.add_subplot(gs[0, 0]); ap = fig.add_subplot(gs[0, 1], sharey=ax); av = fig.add_subplot(gs[1, 0], sharex=ax)
    T.apply(fig, [ax, ap, av])
    for n, (s, col) in ma.items():
        ax.plot(x, s.loc[d.index].values, color=col, lw=2.2 if "200" in n else 1.6, label=n, zorder=1)
    T.candles(ax, d)
    for v, st in [(vah, "--"), (mid[poc], "-"), (val, "--")]:
        ax.axhline(v, color="#7a5cb8", ls=st, lw=1, alpha=.6)
    cols = ["#9575cd" if k in va else "#d6cde6" for k in range(40)]; cols[poc] = "#4b2c91"
    ap.barh(mid, vp / 1e6, height=(bins[1] - bins[0]) * .9, color=cols)
    bb = dict(boxstyle="round,pad=0.15", fc=T.BG, ec="none", alpha=.9)
    ap.text(vp.max() / 1e6, mid[poc], f"POC {mid[poc]:.1f}", ha="right", va="bottom", fontsize=9, color="#4b2c91", fontweight="bold", bbox=bb)
    ap.text(vp.max() / 1e6, vah, f"VAH {vah:.1f}", ha="right", va="bottom", fontsize=8.5, color="#7a5cb8", bbox=bb)
    ap.text(vp.max() / 1e6, val, f"VAL {val:.1f}", ha="right", va="top", fontsize=8.5, color="#7a5cb8", bbox=bb)
    ap.set_title("価格帯別出来高\n（直近40営業日）", fontsize=10); ap.tick_params(labelleft=False, left=False)
    ap.set_xticks([]); ap.grid(False); ap.spines[["bottom"]].set_visible(False)
    ax.set_title(f"{sym} 日足（{date}）", fontsize=13, fontweight="bold", loc="left")
    ax.legend(frameon=False, fontsize=8.5, loc="lower left", ncol=4); ax.tick_params(labelbottom=False)
    up = (d.Close >= d.Open).values
    av.bar(x, d.Volume / 1e6, color=[T.VOL_UP if u else T.VOL_DOWN for u in up], edgecolor=T.VOL_EDGE, lw=.7, width=.7)
    av.plot(x, (d.Volume / 1e6).rolling(20).mean(), color=T.ACCENT, lw=1.4, label="20日平均")
    av.set_ylabel("出来高（百万株）"); av.legend(frameon=False, fontsize=9, loc="upper left")
    tk = list(x[::10]) + ([len(d) - 1] if len(d) - 1 - x[::10][-1] > 4 else [])
    av.set_xticks(tk); av.set_xticklabels([dates[k] for k in tk])
    fig.savefig(out_png, bbox_inches="tight", facecolor=T.BG); plt.close(fig)

    last = full.iloc[-1]
    vol20 = float(full.Volume.iloc[-21:-1].mean())
    return dict(close=round(float(last.Close), 2), low=round(float(last.Low), 2), high=round(float(last.High), 2),
                ma={n: round(float(s.iloc[-1]), 2) for n, (s, _) in ma.items()},
                dist_from_ma={n: round(pct(float(last.Close), float(s.iloc[-1])), 2) for n, (s, _) in ma.items()},
                volume_m=round(float(last.Volume) / 1e6, 1), volume_vs_20d=round(float(last.Volume) / vol20, 2),
                profile=dict(poc=round(float(mid[poc]), 2), vah=round(vah, 2), val=round(val, 2), sessions=40),
                high_52w=round(float(full.High.iloc[-252:].max()), 2), chart=out_png)


def _dl(syms, date, days):
    px = yf.download(syms, start=pd.Timestamp(date) - pd.Timedelta(days=days), end=pd.Timestamp(date) + pd.Timedelta(days=1),
                     progress=False, auto_adjust=True)["Close"].ffill()
    return px.loc[:pd.Timestamp(date)]


def _xdates(ax, idx, n=6):
    k = np.linspace(0, len(idx) - 1, n).astype(int)
    ax.set_xticks([idx[j] for j in k]); ax.set_xticklabels([idx[j].strftime("%m/%d") for j in k])


def _last(ax, s, color="#222", fmt="{:.2f}"):
    ax.annotate(fmt.format(s.iloc[-1]), (s.index[-1], s.iloc[-1]), xytext=(6, 0), textcoords="offset points",
                va="center", fontsize=10, fontweight="bold", color=color)


def macro_chart(date):
    """10年債利回りと SPY（直近およそ3か月）。"""
    px = _dl(["^TNX", "SPY"], date, 100).dropna()
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 5.5), dpi=120, sharex=True, gridspec_kw=dict(hspace=0.12))
    T.apply(fig, [a1, a2])
    for a in (a1, a2):
        a.axvspan(px.index[-6], px.index[-1], color=T.ACCENT, alpha=.12, lw=0)
    a1.plot(px.index, px["^TNX"], color=T.SMA200, lw=2); a1.set_ylabel("米10年債 利回り（%）")
    _last(a1, px["^TNX"], T.SMA200, "{:.2f}%")
    a1.set_title(f"金利と株（{date}まで）　オレンジの帯 = 直近5営業日", fontsize=13, fontweight="bold", loc="left")
    a2.plot(px.index, px["SPY"], color="#222", lw=1.8); a2.set_ylabel("SPY")
    _last(a2, px["SPY"])
    _xdates(a2, px.index)
    out = os.path.join(OUT_DIR, f"{date}_macro.png"); fig.savefig(out, bbox_inches="tight", facecolor=T.BG); plt.close(fig)
    return out


def sentiment_chart(date):
    """VIX と Fear & Greed（fg_{date}.json に history があれば並べる）。"""
    vix = _dl(["^VIX", "SPY"], date, 120)["^VIX"].dropna()
    hist = None
    p = os.path.join(OUT_DIR, f"fg_{date}.json")
    if os.path.exists(p):
        h = json.load(open(p, encoding="utf-8")).get("history")
        if h:
            hist = pd.Series({pd.Timestamp(d): v for d, v in h}).sort_index()
            hist = hist[hist.index >= vix.index[0]]
    n = 2 if hist is not None and len(hist) else 1
    fig, axs = plt.subplots(n, 1, figsize=(12, 2.9 * n + .3), dpi=120, sharex=True, gridspec_kw=dict(hspace=0.15))
    axs = list(np.atleast_1d(axs)); T.apply(fig, axs)
    a = axs[0]
    a.plot(vix.index, vix, color="#222", lw=1.8); a.set_ylabel("VIX")
    for y in (18, 25, 35):
        a.axhline(y, color=T.ACCENT if y > 18 else "#999", ls="--", lw=1, alpha=.7)
        a.text(vix.index[0], y, f" {y}", va="bottom", fontsize=8.5, color="#777")
    _last(a, vix)
    a.set_title(f"投資家心理（{date}まで）　点線 = 判定の目安", fontsize=13, fontweight="bold", loc="left")
    if n == 2:
        b = axs[1]
        b.axhspan(0, 25, color="#c0392b", alpha=.08, lw=0); b.axhspan(75, 100, color="#27ae60", alpha=.08, lw=0)
        b.plot(hist.index, hist.values, color=T.ACCENT, lw=1.8); b.set_ylim(0, 100); b.set_ylabel("Fear & Greed")
        b.text(hist.index[0], 4, " 極度の恐怖", fontsize=9, color="#c0392b"); b.text(hist.index[0], 88, " 極度の強欲", fontsize=9, color="#27ae60")
        _last(b, hist, T.ACCENT, "{:.1f}")
    _xdates(axs[-1], vix.index)
    out = os.path.join(OUT_DIR, f"{date}_sentiment.png"); fig.savefig(out, bbox_inches="tight", facecolor=T.BG); plt.close(fig)
    return out


def sector_chart(date, sec):
    """セクター ETF の5営業日の騰落（グロース・シクリカル・ディフェンシブで色分け）。"""
    syms = GROWTH + CYCLICAL + DEFENSIVE
    px = _dl(syms, date, 20)
    r5 = {s: pct(px[s].iloc[-1], px[s].iloc[-6]) for s in syms}
    col = {**{s: "#c0392b" for s in GROWTH}, **{s: "#9e9e9e" for s in CYCLICAL}, **{s: "#2c6fbb" for s in DEFENSIVE}}
    order = sorted(r5, key=r5.get)
    fig, ax = plt.subplots(figsize=(12, 5), dpi=120); T.apply(fig, [ax])
    y = np.arange(len(order))
    ax.barh(y, [r5[s] for s in order], color=[col[s] for s in order], height=.65)
    ax.set_yticks(y); ax.set_yticklabels([f"{SECTOR_JA[s]}（{s}）" for s in order])
    for k, s in enumerate(order):
        v = r5[s]
        ax.text(v + (.12 if v >= 0 else -.12), k, f"{v:+.2f}%", va="center", ha="left" if v >= 0 else "right", fontsize=9)
    ax.axvline(0, color="#444", lw=1)
    lo, hi = min(r5.values()), max(r5.values()); pad = (hi - lo) * .15 + .5
    ax.set_xlim(min(lo, 0) - pad, max(hi, 0) + pad)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=c, label=f"{g}（平均 {sec[k]:+.2f}%）") for g, c, k in
                       [("グロース", "#c0392b", "growth_5d"), ("シクリカル", "#9e9e9e", "cyclical_5d"), ("ディフェンシブ", "#2c6fbb", "defensive_5d")]],
              frameon=False, loc="upper left", fontsize=9.5)
    ax.set_title(f"セクターの5営業日の騰落（{date}まで）", fontsize=13, fontweight="bold", loc="left")
    ax.grid(axis="y", visible=False)
    out = os.path.join(OUT_DIR, f"{date}_sector.png"); fig.savefig(out, bbox_inches="tight", facecolor=T.BG); plt.close(fig)
    return out


def gex_chart(date, n=25):
    """SPY・QQQ の終値と、境界線・Put Wall・Call Wall の推移（手元の levels JSON から）。"""
    days = sorted(d for d in os.listdir(LEVELS) if d[:4].isdigit() and d <= date)[-n:]
    fig, axs = plt.subplots(2, 1, figsize=(12, 7), dpi=120, sharex=True, gridspec_kw=dict(hspace=0.18))
    T.apply(fig, list(axs))
    for ax, sym in zip(axs, ["SPY", "QQQ"]):
        rows = []
        for d in days:
            f = os.path.join(LEVELS, d, f"{sym}.json")
            if not os.path.exists(f):
                continue
            j = json.load(open(f, encoding="utf-8")); lv = j["levels"]
            rows.append((pd.Timestamp(d), j["spotPrice"], lv.get("hvl"), lv.get("putWall"), lv.get("callWall")))
        df = pd.DataFrame(rows, columns=["d", "spot", "hvl", "pw", "cw"]).set_index("d")
        x = np.arange(len(df))
        ax.fill_between(x, df.hvl, df.spot, where=df.spot >= df.hvl, color="#27ae60", alpha=.12, lw=0, interpolate=True)
        ax.fill_between(x, df.hvl, df.spot, where=df.spot < df.hvl, color="#c0392b", alpha=.12, lw=0, interpolate=True)
        ax.step(x, df.cw, where="mid", color="#27ae60", lw=1.3, ls="--", label="Call Wall")
        ax.step(x, df.pw, where="mid", color="#c0392b", lw=1.3, ls="--", label="Put Wall")
        ax.plot(x, df.hvl, color=T.ACCENT, lw=2, label="境界線（HVL）")
        ax.plot(x, df.spot, color="#222", lw=2, marker="o", ms=3.5, label="終値")
        last = df.iloc[-1]
        for v, c, fmt in [(last.spot, "#222", "{:.2f}"), (last.hvl, T.ACCENT, "{:.2f}")]:
            ax.annotate(fmt.format(v), (x[-1], v), xytext=(6, 0), textcoords="offset points", va="center",
                        fontsize=9.5, fontweight="bold", color=c)
        ax.set_ylabel(sym)
        ax.set_xticks(x[::4]); ax.set_xticklabels([t.strftime("%m/%d") for t in df.index[::4]])
    axs[-1].legend(frameon=False, fontsize=9.5, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=4)
    axs[0].set_title(f"需給（オプション）：終値と境界線（{date}まで）　緑 = 境界線の上（安定）、赤 = 下（不安定）",
                     fontsize=12.5, fontweight="bold", loc="left")
    out = os.path.join(OUT_DIR, f"{date}_gex.png"); fig.savefig(out, bbox_inches="tight", facecolor=T.BG); plt.close(fig)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    date = ap.parse_args().date
    os.makedirs(OUT_DIR, exist_ok=True)
    syms = ["^TNX", "^VIX", "DX-Y.NYB", "CL=F", "SPY"] + GROWTH + DEFENSIVE + CYCLICAL
    px = yf.download(syms, start=pd.Timestamp(date) - pd.Timedelta(days=40), end=pd.Timestamp(date) + pd.Timedelta(days=1),
                     progress=False, auto_adjust=True)["Close"].ffill()
    i = px.index.get_loc(pd.Timestamp(date))
    faces = dict(macro=face_macro(px, i), sentiment=face_sentiment(px, i, date), gex=face_gex(date),
                 strength=face_strength(date), sector=face_sector(px, i))
    ws = [f["weather"] for f in faces.values() if f["weather"]]
    total = sum(SCORE[w] for w in ws)
    view = "攻め" if total >= 2 else ("守り" if total <= -2 else "様子見")
    n_alarm = sum(w == "警報" for w in ws)
    res = dict(date=date, faces=faces, total=total, view=view, n_faces=len(ws), n_alarm=n_alarm,
               technical={s: technical(s, date, os.path.join(OUT_DIR, f"{date}_{s}_tech.png")) for s in TECH_SYMS},
               charts=dict(macro=macro_chart(date), sentiment=sentiment_chart(date), sector=sector_chart(date, faces["sector"]), gex=gex_chart(date) if faces["gex"]["weather"] else None))
    out = os.path.join(OUT_DIR, f"{date}.json")
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print(f"FRAMEWORK={out}")
    for k, f in faces.items():
        print(f"  {k}: {f['weather'] or '計算が間に合っていない'}")
    print(f"  合計 {total}（{len(ws)}面）→ {view}")


if __name__ == "__main__":
    main()
