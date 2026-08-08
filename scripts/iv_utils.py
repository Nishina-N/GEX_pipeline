"""IVサマリの読み出しと σ（固定テナーのATM IV）算出の共通処理.

タスク#8（IV脆弱性 σ_20MA）と #13（確率コーン）で σ を共有するためのモジュール。

データ源: `data/r2/iv_history/{symbol}/{date}.json`
（`scripts/pull_iv_history.py` が R2 から吸い出したIVサマリ。
 R2 側は30日で消えるのでローカルのアーカイブが正）

サマリの構造:
    {"date","symbol","spotPrice",
     "expirations":[{"expiration","dte","atm_iv","put_25d_iv","call_25d_iv","skew"}, ...]}
"""
import json
import math
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IV_DIR = ROOT / "data" / "r2" / "iv_history"

DEFAULT_TENOR = 30  # 日。確率コーン・σ_20MA の基準テナー


def load_summary(symbol: str, date_str: str) -> dict | None:
    p = IV_DIR / symbol / f"{date_str}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def available_dates(symbol: str) -> list[str]:
    d = IV_DIR / symbol
    if not d.exists():
        return []
    return sorted(f.stem for f in d.glob("*.json"))


def available_symbols() -> list[str]:
    if not IV_DIR.exists():
        return []
    return sorted(p.name for p in IV_DIR.iterdir() if p.is_dir())


def atm_iv_at_tenor(summary: dict, tenor_days: int = DEFAULT_TENOR) -> float | None:
    """ATM IV を指定テナー（日数）に補間して返す。年率の小数（0.15 = 15%）。

    テナーを固定しないと、日々どの満期を見るかで σ が跳ねて時系列比較にならない。
    補間は**分散 × 満期（total variance）を dte に対して線形**に行う（IVそのものの
    線形補間より期間構造の扱いが素直）。テナー範囲外は最近傍の満期で外挿せず、
    端点の IV をそのまま使う。
    """
    pts = []
    for e in summary.get("expirations", []):
        dte, iv = e.get("dte"), e.get("atm_iv")
        # dte=0 は atm_iv=0.0 で入ってくることがある（当日満期のATM欠損）→ 除外
        if dte is None or not iv or dte <= 0 or iv <= 0:
            continue
        pts.append((float(dte), float(iv)))
    if not pts:
        return None
    pts.sort()

    if tenor_days <= pts[0][0]:
        return pts[0][1]
    if tenor_days >= pts[-1][0]:
        return pts[-1][1]

    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        if d0 <= tenor_days <= d1:
            # total variance の線形補間
            w0, w1 = v0 ** 2 * d0, v1 ** 2 * d1
            w = w0 + (w1 - w0) * (tenor_days - d0) / (d1 - d0)
            return math.sqrt(max(w, 1e-12) / tenor_days)
    return None


def skew_at_tenor(summary: dict, tenor_days: int = DEFAULT_TENOR) -> float | None:
    """25デルタのスキュー（put_25d_iv - call_25d_iv）を指定テナー近傍で返す。"""
    best, bestd = None, None
    for e in summary.get("expirations", []):
        dte, sk = e.get("dte"), e.get("skew")
        if dte is None or sk is None or dte <= 0:
            continue
        d = abs(dte - tenor_days)
        if bestd is None or d < bestd:
            best, bestd = float(sk), d
    return best


def sigma_series(symbol: str, tenor_days: int = DEFAULT_TENOR) -> list[tuple[str, float]]:
    """(date, σ) の時系列を古い順に返す。"""
    out = []
    for d in available_dates(symbol):
        s = load_summary(symbol, d)
        if not s:
            continue
        v = atm_iv_at_tenor(s, tenor_days)
        if v:
            out.append((d, v))
    return out


def iv_vulnerability(symbol: str, date_str: str, window: int = 20,
                     tenor_days: int = DEFAULT_TENOR) -> dict | None:
    """タスク#8: IV脆弱性 = σ_today / median_N(σ) を返す.

    IVが平常より**低い**ほど「油断＝脆弱」（小さなショックでIVが跳ねる余地が大きい）、
    高いほど「すでに織り込み済み」。GEXの厚み比とは別軸の指標として併読する。
    """
    ser = sigma_series(symbol, tenor_days)
    idx = next((i for i, (d, _) in enumerate(ser) if d == date_str), None)
    if idx is None or idx < window:
        return None
    hist = [v for _, v in ser[idx - window:idx]]
    med = st.median(hist)
    if med <= 0:
        return None
    sigma = ser[idx][1]
    ratio = sigma / med
    if ratio < 0.85:
        label = "低IV（油断／跳ねしろ大）"
    elif ratio > 1.15:
        label = "高IV（すでに警戒織り込み）"
    else:
        label = "平常"
    return {
        "symbol": symbol, "date": date_str, "tenor_days": tenor_days,
        "sigma": sigma, "median": med, "ratio": ratio, "label": label,
        "n_hist": len(hist),
    }
