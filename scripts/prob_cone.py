"""確率コーン（タスク#13）— IVから対数正規で到達確率を算出・可視化.

moomooの「確率分析」と同じ枠組み（Black-Scholesと同一の対数正規/GBM仮定）:
    ln(S_T/S_0) ~ N(-½σ²T, σ²T)
    ±1σ帯 ≒ S_0 · σ · √T            (68.27%)
    終値確率  P(S_T > K) = N( (ln(S_0/K) - ½σ²T) / (σ√T) )

**GEXとの接続がこのスクリプトの主眼**: Call Wall / Put Wall / HVL に対して
「満期時点で超えている確率（終値ベース）」と「期間中に一度でも触る確率（到達ベース）」
の両方を出す。壁は「触るか」が問題なので、到達確率のほうが実務的に効く。

    python scripts/prob_cone.py SPY --date 2026-08-07
    python scripts/prob_cone.py SPY --date 2026-08-07 --chart out.png

前提の限界:
- 単一σの対数正規なのでスキュー（下方向IV高）を無視している。下落側の確率は
  実際にはこれより高く出るのが普通。IVサマリの skew を参考値として併記する。
- 「未来予測」ではなく「今のIVが正しいとした場合の確率」。
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import iv_utils as iv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LEVELS_DIR = ROOT / "data" / "r2" / "gex" / "daily"


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_above_at_expiry(spot: float, k: float, sigma: float, T: float) -> float:
    """P(S_T > K)。T は年。"""
    if T <= 0 or sigma <= 0:
        return 1.0 if spot > k else 0.0
    d = (math.log(spot / k) - 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d)


def prob_touch(spot: float, k: float, sigma: float, T: float) -> float:
    """期間中に一度でも K に触れる確率（ドリフトなしGBMの初到達確率）.

    ドリフト0の対数価格ではリフレクション原理により touch ≈ 2 × 終値確率。
    ここでは ½σ²T のドリフトを残したまま扱うため、上下それぞれ
    「終値で超える確率の2倍（上限1.0）」で近似する。
    """
    if T <= 0 or sigma <= 0:
        return 1.0 if (k >= spot) == (spot >= k) else 0.0
    if k >= spot:
        p = prob_above_at_expiry(spot, k, sigma, T)
    else:
        p = 1.0 - prob_above_at_expiry(spot, k, sigma, T)
    return min(1.0, 2.0 * p)


CONE_TENORS = (5, 10, 21)   # 営業日。levels JSON に載せる期間


def build_probability_block(summary: dict, spot: float, levels: dict,
                            tenors=CONE_TENORS) -> dict | None:
    """levels JSON に載せる `probability` ブロックを組み立てる.

    `3_extract_levels.py` から呼ばれ、R2 の levels JSON に同梱される。
    これによりチャート生成（visualize_gex）も記事生成も、IVアーカイブを
    参照せずに σ と到達確率を使える。

    注意: IV脆弱性（σ_20MA）は20営業日の履歴が要るためここには入れない。
    クラウド側は毎回まっさらで履歴を持たないので、ローカルで
    `iv_utils.iv_vulnerability()` を使って別途算出する。
    """
    if not summary or not spot:
        return None

    block = {
        "sigma": {},
        "skew_25d": iv.skew_at_tenor(summary),
        "cone": {},
        "levels": {},
        "note": "対数正規(GBM)前提。単一σのためスキューは未反映で、下落側の確率は実際より低めに出る。",
    }

    targets = {k: levels.get(v) for k, v in
               (("CW", "callWall"), ("PW", "putWall"), ("HVL", "hvl"))}
    targets = {k: v for k, v in targets.items() if v}

    for days in tenors:
        # 営業日 → 暦日に直してIV期間構造を引く
        sigma = iv.atm_iv_at_tenor(summary, max(1, int(days * 7 / 5)))
        if not sigma:
            continue
        key = str(days)
        T = days / 252.0
        band = spot * sigma * math.sqrt(T)
        block["sigma"][key] = round(sigma, 4)
        block["cone"][key] = {
            "s1_low": round(spot - band, 2), "s1_high": round(spot + band, 2),
            "s2_low": round(spot - 2 * band, 2), "s2_high": round(spot + 2 * band, 2),
        }
        for name, k in targets.items():
            p_above = prob_above_at_expiry(spot, k, sigma, T)
            up = k >= spot
            block["levels"].setdefault(name, {"strike": k, "side": "上" if up else "下"})
            block["levels"][name][key] = {
                "p_touch": round(prob_touch(spot, k, sigma, T), 4),
                "p_beyond": round(p_above if up else 1.0 - p_above, 4),
            }

    return block if block["sigma"] else None


def load_levels(symbol: str, date_str: str) -> dict | None:
    p = LEVELS_DIR / date_str / f"{symbol}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def analyze(symbol: str, date_str: str, horizons=(5, 10, 21)):
    summary = iv.load_summary(symbol, date_str)
    if not summary:
        raise SystemExit(f"IVサマリがありません: {symbol} {date_str}"
                         f"（scripts/pull_iv_history.py を先に実行）")
    lv = load_levels(symbol, date_str)
    spot = (lv or {}).get("spotPrice") or summary.get("spotPrice")
    if not spot:
        raise SystemExit("spotPrice が取得できません")

    out = {"symbol": symbol, "date": date_str, "spot": spot,
           "skew_30d": iv.skew_at_tenor(summary), "horizons": []}

    vuln = iv.iv_vulnerability(symbol, date_str)
    out["vulnerability"] = vuln

    levels = (lv or {}).get("levels") or {}
    targets = {k: levels.get(v) for k, v in
               (("CW", "callWall"), ("PW", "putWall"), ("HVL", "hvl"))}
    targets = {k: v for k, v in targets.items() if v}

    for days in horizons:
        sigma = iv.atm_iv_at_tenor(summary, days)
        if not sigma:
            continue
        T = days / 365.0
        band = spot * sigma * math.sqrt(T)
        h = {"days": days, "sigma": sigma,
             "sigma1_low": spot - band, "sigma1_high": spot + band,
             "sigma2_low": spot - 2 * band, "sigma2_high": spot + 2 * band,
             "levels": {}}
        for name, k in targets.items():
            p_above = prob_above_at_expiry(spot, k, sigma, T)
            # 水準が上にあるなら「上抜け」、下にあるなら「割れ」で見るのが実務的
            up = k >= spot
            h["levels"][name] = {
                "strike": k,
                "side": "上" if up else "下",
                "p_above_expiry": p_above,
                # 満期時点でその水準の「向こう側」にいる確率
                "p_beyond_expiry": p_above if up else 1.0 - p_above,
                "p_touch": prob_touch(spot, k, sigma, T),
            }
        out["horizons"].append(h)
    return out


def render_text(r: dict) -> str:
    L = [f"{r['symbol']}  {r['date']}  Spot {r['spot']:.2f}"]
    v = r.get("vulnerability")
    if v:
        L.append(f"IV脆弱性: σ30={v['sigma']*100:.1f}%  平常比 {v['ratio']:.2f}  → {v['label']}")
    if r.get("skew_30d") is not None:
        L.append(f"25Δスキュー(30d近傍): {r['skew_30d']:+.4f}"
                 f"（プラス=下方向のIVが高い＝下落側の確率は下表より高め）")
    for h in r["horizons"]:
        L.append("")
        L.append(f"── 残存 {h['days']}営業日相当  σ={h['sigma']*100:.1f}%")
        L.append(f"   ±1σ (68%): {h['sigma1_low']:.2f} 〜 {h['sigma1_high']:.2f}")
        L.append(f"   ±2σ (95%): {h['sigma2_low']:.2f} 〜 {h['sigma2_high']:.2f}")
        for name, d in h["levels"].items():
            verb = "上抜け" if d["side"] == "上" else "割れ"
            L.append(f"   {name:3} {d['strike']:>9.2f} ({d['side']})  "
                     f"期間中に到達 {d['p_touch']*100:5.1f}%  /  "
                     f"満期時に{verb}ている {d['p_beyond_expiry']*100:5.1f}%")
    L.append("")
    L.append("※ 単一σの対数正規による「今のIVが正しい前提での確率」。将来予測ではない。")
    return "\n".join(L)


def render_chart(r: dict, path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    spot = r["spot"]
    maxd = max(h["days"] for h in r["horizons"])
    sig = r["horizons"][-1]["sigma"]
    t = np.linspace(0, maxd, 100)
    band = spot * sig * np.sqrt(t / 365.0)

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.fill_between(t, spot - 2 * band, spot + 2 * band, alpha=.15,
                    color="#3b82f6", label="±2σ (95%)")
    ax.fill_between(t, spot - band, spot + band, alpha=.30,
                    color="#3b82f6", label="±1σ (68%)")
    ax.axhline(spot, color="#f59e0b", lw=1.5, label=f"Spot {spot:.2f}")

    # ラベルは英語（既存チャートに合わせる。日本語フォントは未設定のため豆腐になる）
    colors = {"CW": "#16a34a", "PW": "#dc2626", "HVL": "#334155"}
    last = r["horizons"][-1]["levels"]
    # 水準が近接すると注記が重なるので、描画順に最低間隔を空けてずらす
    lo, hi = ax.get_ylim()
    min_gap = (hi - lo) * 0.045
    placed = []
    for name, d in sorted(last.items(), key=lambda kv: kv[1]["strike"]):
        ax.axhline(d["strike"], color=colors.get(name, "#666"), ls="--", lw=1.2)
        y = d["strike"]
        while placed and y - placed[-1] < min_gap:
            y = placed[-1] + min_gap
        placed.append(y)
        ax.annotate(f"{name} {d['strike']:.2f}  touch {d['p_touch']*100:.0f}%",
                    xy=(maxd, d["strike"]), xytext=(maxd * 1.02, y),
                    va="center", fontsize=9, color=colors.get(name, "#666"),
                    arrowprops=dict(arrowstyle="-", lw=.6,
                                    color=colors.get(name, "#666")))

    ax.set_xlabel("Trading days ahead")
    ax.set_ylabel("Price")
    ax.set_title(f"{r['symbol']} probability cone  {r['date']}  "
                 f"(sigma={sig*100:.1f}%)")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(path, dpi=120, bbox_inches="tight")
    print(f"chart → {path}")


def main():
    ap = argparse.ArgumentParser(description="確率コーン (タスク#13)")
    ap.add_argument("symbol")
    ap.add_argument("--date", required=True)
    ap.add_argument("--chart", help="PNG 出力先")
    ap.add_argument("--json", action="store_true", help="JSONで出力")
    a = ap.parse_args()

    r = analyze(a.symbol.upper(), a.date)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(render_text(r))
    if a.chart:
        render_chart(r, a.chart)


if __name__ == "__main__":
    main()
