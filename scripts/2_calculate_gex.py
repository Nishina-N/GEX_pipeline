"""
2_calculate_gex.py

オプションチェーンデータからGEX（ガンマエクスポージャー）を計算する。
Barone-Adesi Whaley (BAW) モデルでガンマを計算し、ストライク別の Net GEX を集計する。

集計タイプ:
  - 全体 (gex_by_strike): 全満期の合算
  - 短期 (gex_short_term): DTE 0-7日の満期を合算
  - 長期 (gex_long_term): 次の2つの月次SQ（第3金曜）を合算
"""

import os
import sys
import json
import pickle
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy.stats import norm

from market_calendar import get_pipeline_date

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

DATA_FOLDER = "data"
OPTIONS_DIR = os.path.join(DATA_FOLDER, "options")
GEX_DIR = os.path.join(DATA_FOLDER, "gex")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "settings.json")


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────
# Black-Scholes 補助関数（BAW内部で使用）
# ─────────────────────────────────────────────────────────────

def _bs_price(S, K, T, b, r, sigma, option_type):
    """
    Black-Scholes価格（コスト・オブ・キャリー b = r - q を使用）。
    S, K, T, b, r, sigma はすべてスカラーを想定。
    """
    d1 = (np.log(S / K) + (b + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if option_type == 'call':
        return S * np.exp((b - r) * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp((b - r) * T) * norm.cdf(-d1)


# ─────────────────────────────────────────────────────────────
# BAW モデル
# ─────────────────────────────────────────────────────────────

def _baw_q_params(T, r, q, sigma):
    """
    BAW補助パラメータ (q1, q2) を算出する。
      M = 2r/σ²,  N = 2(r-q)/σ²,  k = 1 - e^{-rT}
      q2 = (-(N-1) + sqrt((N-1)² + 4M/k)) / 2  > 0
      q1 = (-(N-1) - sqrt((N-1)² + 4M/k)) / 2  < 0
    """
    b = r - q
    M = 2.0 * r / sigma ** 2
    N = 2.0 * b / sigma ** 2
    k = 1.0 - np.exp(-r * T)
    # k が 0 に近い場合（r≈0 または T≈0）のガード
    if k < 1e-10:
        k = 1e-10
    discriminant = max((N - 1) ** 2 + 4.0 * M / k, 0.0)
    sq = np.sqrt(discriminant)
    q2 = (-(N - 1) + sq) / 2.0
    q1 = (-(N - 1) - sq) / 2.0
    return q1, q2


def _find_call_critical(K, T, b, r, sigma, q2, max_iter=50, tol=1e-6):
    """
    ニュートン法でコールの早期行使臨界価格 S* を求める。
    条件: S* - K = C_BS(S*) + (S*/q2) * (1 - e^{(b-r)T} * N(d1(S*)))
    S* > K が保証されるようクランプする。
    """
    # 初期値: ATM近傍のやや高め
    S = K * (1.0 + sigma * np.sqrt(T))
    S = max(S, K * 1.001)

    for _ in range(max_iter):
        d1 = (np.log(S / K) + (b + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        C_bs = _bs_price(S, K, T, b, r, sigma, 'call')
        early_factor = np.exp((b - r) * T) * norm.cdf(d1)

        f = (S - K) - C_bs - (S / q2) * (1.0 - early_factor)

        # df/dS の解析的導関数
        d_Cbs_dS = np.exp((b - r) * T) * norm.cdf(d1)            # コールのデルタ
        d_early_dS = np.exp((b - r) * T) * norm.pdf(d1) / (S * sigma * np.sqrt(T))
        df = 1.0 - d_Cbs_dS - (1.0 / q2) * (1.0 - early_factor) + (S / q2) * d_early_dS

        if abs(df) < 1e-12:
            break

        S_new = S - f / df
        S_new = max(S_new, K * 1.001)

        if abs(S_new - S) < tol:
            return S_new
        S = S_new

    return S


def _find_put_critical(K, T, b, r, sigma, q1, max_iter=50, tol=1e-6):
    """
    ニュートン法でプットの早期行使臨界価格 S** を求める。
    条件: K - S** = P_BS(S**) - (S**/q1) * (1 - e^{(b-r)T} * N(-d1(S**)))
    0 < S** < K が保証されるようクランプする。
    """
    # 初期値: ATMよりやや低め
    S = K * max(0.5, 1.0 - sigma * np.sqrt(T))
    S = min(S, K * 0.999)
    S = max(S, K * 0.001)

    for _ in range(max_iter):
        d1 = (np.log(S / K) + (b + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        P_bs = _bs_price(S, K, T, b, r, sigma, 'put')
        early_factor = np.exp((b - r) * T) * norm.cdf(-d1)

        f = (K - S) - P_bs + (S / q1) * (1.0 - early_factor)

        # df/dS の解析的導関数
        # dP_bs/dS = -exp((b-r)T) * N(-d1) = -early_factor
        # d(early_factor)/dS = -exp((b-r)T) * N'(d1) / (S * sigma * sqrt(T))
        d_early_dS = -np.exp((b - r) * T) * norm.pdf(d1) / (S * sigma * np.sqrt(T))
        df = -1.0 + early_factor + (1.0 / q1) * (1.0 - early_factor) - (S / q1) * d_early_dS

        if abs(df) < 1e-12:
            break

        S_new = S - f / df
        S_new = min(S_new, K * 0.999)
        S_new = max(S_new, K * 0.001)

        if abs(S_new - S) < tol:
            return S_new
        S = S_new

    return S


def _baw_price_given_critical(S, K, T, b, r, sigma, q1, q2, S_critical, option_type):
    """
    事前計算済みの臨界価格・q1/q2 を使って BAW 価格を返す。
    Newton 計算をスキップできるため、数値微分での 3 点評価に使用する。
    """
    C_or_P_bs = _bs_price(S, K, T, b, r, sigma, option_type)

    if option_type == 'call':
        if S >= S_critical:
            return max(S - K, 0.0)
        d1_crit = (np.log(S_critical / K) + (b + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        A2 = (S_critical / q2) * (1.0 - np.exp((b - r) * T) * norm.cdf(d1_crit))
        return C_or_P_bs + A2 * (S / S_critical) ** q2
    else:
        if S <= S_critical:
            return max(K - S, 0.0)
        d1_crit = (np.log(S_critical / K) + (b + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        A1 = -(S_critical / q1) * (1.0 - np.exp((b - r) * T) * norm.cdf(-d1_crit))
        return C_or_P_bs + A1 * (S / S_critical) ** q1


def baw_gamma(S, K, T, r, q, sigma, option_type):
    """
    BAW (Barone-Adesi Whaley) ガンマを数値二次微分で算出する。

      Γ = (V(S+ΔS) - 2·V(S) + V(S-ΔS)) / ΔS²   (ΔS = S × 0.001)

    臨界価格 S* (または S**) はニュートン法で 1 回だけ求め、
    3 点の価格評価で再利用することで計算コストを削減する。
    """
    sigma = max(float(sigma), 0.001)
    T = max(float(T), 1.0 / 365)
    b = r - q
    dS = S * 0.001

    # 深すぎる OTM はガンマ ≈ 0（計算スキップで高速化）
    moneyness_sigma = abs(np.log(S / K)) / (sigma * np.sqrt(T))
    if moneyness_sigma > 5.0:
        return 0.0

    # BAW パラメータを一度だけ計算
    q1, q2 = _baw_q_params(T, r, q, sigma)

    if option_type == 'call':
        if b >= r:
            # 配当なしのコール: 早期行使は最適でない → BSガンマを数値微分で代替
            # (BSの場合も数値微分で統一)
            S_crit = float('inf')
        else:
            S_crit = _find_call_critical(K, T, b, r, sigma, q2)
    else:
        S_crit = _find_put_critical(K, T, b, r, sigma, q1)

    # 3 点評価（S_crit は共有）
    V_up = _baw_price_given_critical(S + dS, K, T, b, r, sigma, q1, q2, S_crit, option_type)
    V_mid = _baw_price_given_critical(S, K, T, b, r, sigma, q1, q2, S_crit, option_type)
    V_dn = _baw_price_given_critical(S - dS, K, T, b, r, sigma, q1, q2, S_crit, option_type)

    gamma = (V_up - 2.0 * V_mid + V_dn) / (dS ** 2)
    return max(gamma, 0.0)  # ガンマは非負


# ─────────────────────────────────────────────────────────────
# 時間軸分類ヘルパー
# ─────────────────────────────────────────────────────────────

def _get_third_friday(year, month):
    """指定年月の第3金曜日を datetime で返す"""
    first = datetime(year, month, 1)
    days_until_fri = (4 - first.weekday()) % 7   # 0=月 ... 4=金
    first_fri = first + timedelta(days=days_until_fri)
    return first_fri + timedelta(days=14)


def get_next_monthly_expirations(from_date, n=2):
    """
    from_date の翌日以降、最初の n 個の月次 SQ 日（第3金曜）を
    'YYYY-MM-DD' 文字列のリストで返す。
    """
    result = []
    year, month = from_date.year, from_date.month
    # 最大 n+3 ヶ月先まで探索
    for _ in range(n + 3):
        tf = _get_third_friday(year, month)
        if tf.date() > from_date.date() and tf.strftime('%Y-%m-%d') not in result:
            result.append(tf.strftime('%Y-%m-%d'))
        if len(result) >= n:
            break
        month += 1
        if month > 12:
            month = 1
            year += 1
    return result[:n]


def classify_expirations(expirations, today):
    """
    満期日リストを短期・長期バケットに分類する。

    Returns:
        short_term (list): DTE 0-7 の満期日
        long_term  (list): 次の2月次SQ に一致する満期日
    """
    short_term = []
    long_term_targets = get_next_monthly_expirations(today, n=2)
    long_term = []

    for exp in expirations:
        exp_dt = datetime.strptime(exp, '%Y-%m-%d')
        dte = (exp_dt.date() - today.date()).days
        if 0 <= dte <= 7:
            short_term.append(exp)
        if exp in long_term_targets:
            long_term.append(exp)

    return short_term, long_term


# ─────────────────────────────────────────────────────────────
# GEX 集計
# ─────────────────────────────────────────────────────────────

def _aggregate_gex_by_strike(chain_subset):
    """
    チェーンのサブセットからストライク別 GEX を集計する。
    Returns:
        DataFrame (strike, callGEX, callOI, putGEX, putOI, netGEX, totalOI)
        or None if chain_subset is empty
    """
    if chain_subset is None or chain_subset.empty:
        return None

    agg = chain_subset.groupby(['strike', 'optionType']).agg(
        gex=('gex', 'sum'),
        oi=('openInterest', 'sum')
    ).reset_index()

    calls = agg[agg['optionType'] == 'call'][['strike', 'gex', 'oi']].rename(
        columns={'gex': 'callGEX', 'oi': 'callOI'}
    )
    puts = agg[agg['optionType'] == 'put'][['strike', 'gex', 'oi']].rename(
        columns={'gex': 'putGEX', 'oi': 'putOI'}
    )

    merged = pd.merge(calls, puts, on='strike', how='outer').fillna(0)
    merged['netGEX'] = merged['callGEX'] + merged['putGEX']
    merged['totalOI'] = merged['callOI'] + merged['putOI']
    return merged.sort_values('strike').reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# メイン計算
# ─────────────────────────────────────────────────────────────

def calculate_gex_for_symbol(options_data, config):
    """
    1銘柄のオプションデータからストライク別 GEX を計算する（BAW モデル使用）。

    GEX 計算式:
        GEX_call(K) = +OI_call × Γ_BAW × Multiplier × S² × 0.01
        GEX_put(K)  = -OI_put  × Γ_BAW × Multiplier × S² × 0.01
        (Multiplier = contract_size = 100 → Multiplier × 0.01 = 1)

    Returns:
        dict: {
            'symbol', 'spot_price', 'date', 'total_gex',
            'gex_by_strike'     : DataFrame (全満期合算),
            'gex_short_term'    : DataFrame (DTE 0-7),
            'gex_long_term'     : DataFrame (次の2月次SQ),
            'gex_0dte'          : DataFrame (当日満期のみ),
            'gex_by_strike_expiry',
            'short_term_expirations', 'long_term_expirations',
            'expirations_used', 'total_contracts'
        }
    """
    symbol = options_data['symbol']
    spot = options_data['spot_price']
    chain = options_data['chain'].copy()

    r = config.get('risk_free_rate', 0.045)
    dividend_yields = config.get('dividend_yields', {})
    q = dividend_yields.get(symbol, config.get('dividend_yield_default', 0.013))
    contract_size = config.get('contract_size', 100)

    today_str = get_pipeline_date()
    today = datetime.strptime(today_str, '%Y-%m-%d')

    # 残存年数を計算
    chain['expiration_dt'] = pd.to_datetime(chain['expiration'])
    chain['T'] = (chain['expiration_dt'] - pd.Timestamp(today_str)) / pd.Timedelta(days=365)
    chain['T'] = chain['T'].clip(lower=1.0 / 365)

    # OI=0 または IV=0 の行を除外
    chain = chain[(chain['openInterest'] > 0) & (chain['impliedVolatility'] > 0)].copy()
    if chain.empty:
        logging.warning(f"[{symbol}] No valid contracts after filtering")
        return None

    logging.info(
        f"[{symbol}] Computing BAW gamma for {len(chain)} contracts "
        f"(spot={spot:.2f}, r={r}, q={q})..."
    )

    # ── BAW ガンマ計算（行ごと） ──────────────────────────────
    # S* はコントラクトごとに 1 回だけ Newton 法で求め、
    # 数値微分の 3 点評価で再利用することで計算量を削減している。
    gammas = [
        baw_gamma(
            S=spot,
            K=float(row['strike']),
            T=float(row['T']),
            r=r,
            q=q,
            sigma=float(row['impliedVolatility']),
            option_type=row['optionType']
        )
        for _, row in chain.iterrows()
    ]
    chain['gamma'] = gammas

    # ── GEX 計算 ──────────────────────────────────────────────
    # GEX = OI × Γ × contract_size × S² × 0.01
    # contract_size(=100) × 0.01 = 1 なので実質 OI × Γ × S²
    chain['gex'] = chain['gamma'] * chain['openInterest'] * contract_size * spot ** 2 * 0.01
    chain.loc[chain['optionType'] == 'put', 'gex'] *= -1

    # ── 時間軸分類 ────────────────────────────────────────────
    all_expirations = sorted(chain['expiration'].unique().tolist())
    short_term_exps, long_term_exps = classify_expirations(all_expirations, today)

    logging.info(f"[{symbol}] Short-term expirations (DTE 0-7): {short_term_exps}")
    logging.info(f"[{symbol}] Long-term expirations (next 2 monthly SQ): {long_term_exps}")

    # ── 各バケットの集計 ──────────────────────────────────────
    gex_by_strike = _aggregate_gex_by_strike(chain)
    total_gex = float(gex_by_strike['netGEX'].sum())

    gex_short_term = _aggregate_gex_by_strike(
        chain[chain['expiration'].isin(short_term_exps)] if short_term_exps else pd.DataFrame()
    )
    gex_long_term = _aggregate_gex_by_strike(
        chain[chain['expiration'].isin(long_term_exps)] if long_term_exps else pd.DataFrame()
    )
    gex_0dte = _aggregate_gex_by_strike(chain[chain['expiration'] == today_str])

    # ── ストライク×満期別集計（後方互換） ───────────────────
    agg_exp = chain.groupby(['strike', 'expiration', 'optionType']).agg(
        gex=('gex', 'sum'),
        oi=('openInterest', 'sum')
    ).reset_index()
    calls_e = agg_exp[agg_exp['optionType'] == 'call'][
        ['strike', 'expiration', 'gex', 'oi']
    ].rename(columns={'gex': 'callGEX', 'oi': 'callOI'})
    puts_e = agg_exp[agg_exp['optionType'] == 'put'][
        ['strike', 'expiration', 'gex', 'oi']
    ].rename(columns={'gex': 'putGEX', 'oi': 'putOI'})
    merged_exp = pd.merge(calls_e, puts_e, on=['strike', 'expiration'], how='outer').fillna(0)
    merged_exp['netGEX'] = merged_exp['callGEX'] + merged_exp['putGEX']

    logging.info(
        f"[{symbol}] GEX calculated: {len(gex_by_strike)} strikes, "
        f"Total Net GEX: {total_gex:,.0f}, "
        f"Short-term strikes: {len(gex_short_term) if gex_short_term is not None else 0}, "
        f"Long-term strikes: {len(gex_long_term) if gex_long_term is not None else 0}"
    )

    return {
        'symbol': symbol,
        'spot_price': spot,
        'date': today_str,
        'total_gex': total_gex,
        'gex_by_strike': gex_by_strike,
        'gex_by_strike_expiry': merged_exp,
        'gex_0dte': gex_0dte,
        'gex_short_term': gex_short_term,
        'gex_long_term': gex_long_term,
        'short_term_expirations': short_term_exps,
        'long_term_expirations': long_term_exps,
        'expirations_used': len(all_expirations),
        'total_contracts': len(chain)
    }


def main():
    config = load_config()
    os.makedirs(GEX_DIR, exist_ok=True)

    logging.info("=" * 60)
    logging.info("CALCULATE GEX  (model: BAW)")
    logging.info("=" * 60)

    if not os.path.exists(OPTIONS_DIR):
        logging.error(f"Options data directory not found: {OPTIONS_DIR}")
        return False

    pkl_files = [f for f in os.listdir(OPTIONS_DIR) if f.endswith('.pkl')]
    if not pkl_files:
        logging.error("No options data files found")
        return False

    success_count = 0
    fail_count = 0

    for pkl_file in pkl_files:
        symbol = pkl_file.replace('.pkl', '')
        pkl_path = os.path.join(OPTIONS_DIR, pkl_file)

        try:
            with open(pkl_path, 'rb') as f:
                options_data = pickle.load(f)

            result = calculate_gex_for_symbol(options_data, config)

            if result:
                output_path = os.path.join(GEX_DIR, f"{symbol}.pkl")
                with open(output_path, 'wb') as f:
                    pickle.dump(result, f)
                success_count += 1
                logging.info(f"[{symbol}] Saved to {output_path}")
            else:
                fail_count += 1

        except Exception as e:
            logging.error(f"[{symbol}] Error: {e}", exc_info=True)
            fail_count += 1

    logging.info("=" * 60)
    logging.info(f"Success: {success_count}, Failed: {fail_count}")
    logging.info("=" * 60)

    return success_count > 0


if __name__ == "__main__":
    if main():
        sys.exit(0)
    else:
        sys.exit(1)
