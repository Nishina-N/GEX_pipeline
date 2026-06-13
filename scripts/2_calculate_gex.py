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
# Black-Scholes 補助関数（BAW内部で使用、ベクトル化）
# ─────────────────────────────────────────────────────────────
#
# 旧実装は 1 コントラクトずつ chain.iterrows() で baw_gamma() を呼び、
# 内部で scipy.stats.norm をスカラー評価していた。本実装は全コントラクトを
# numpy 配列として一括処理する（計算式・許容誤差・反復回数は旧版と同一）。
#
# 数値はベクトル演算の順序差により末尾桁が動き得るが（相対 ~1e-12 以下）、
# Wall/HVL/totalGEX の表示精度には影響しない。
#
# 【バグ修正】旧実装は配当なしコール (b>=r, S_crit=inf) で
#   A2 = inf * (1 - 1) = nan となり gamma=nan を返していた。
# 無配当アメリカンコールは早期行使が最適でない → BS と一致するため、
# 本実装では S_crit=inf のとき早期行使プレミアム=0（純BSガンマ）に帰着させる。


def _bs_price_vec(S, K, T, b, r, sigma, is_call):
    """
    Black-Scholes価格（コスト・オブ・キャリー b = r - q）。配列対応。
    is_call: bool 配列（True=コール / False=プット）。S はスカラーまたは配列。
    """
    sqT = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + (b + 0.5 * sigma ** 2) * T) / sqT
    d2 = d1 - sqT
    call = S * np.exp((b - r) * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    put = K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp((b - r) * T) * norm.cdf(-d1)
    return np.where(is_call, call, put)


# ─────────────────────────────────────────────────────────────
# BAW モデル（ベクトル化）
# ─────────────────────────────────────────────────────────────

def _baw_q_params_vec(T, r, q, sigma):
    """
    BAW補助パラメータ (q1, q2) を配列で算出する。
      M = 2r/σ²,  N = 2(r-q)/σ²,  k = 1 - e^{-rT}
      q2 = (-(N-1) + sqrt((N-1)² + 4M/k)) / 2  > 0
      q1 = (-(N-1) - sqrt((N-1)² + 4M/k)) / 2  < 0
    """
    b = r - q
    M = 2.0 * r / sigma ** 2
    N = 2.0 * b / sigma ** 2
    k = np.maximum(1.0 - np.exp(-r * T), 1e-10)   # r≈0 / T≈0 のガード
    discriminant = np.maximum((N - 1) ** 2 + 4.0 * M / k, 0.0)
    sq = np.sqrt(discriminant)
    q2 = (-(N - 1) + sq) / 2.0
    q1 = (-(N - 1) - sq) / 2.0
    return q1, q2


def _newton_vec(S_init, K, T, b, r, sigma, qparam, is_call, max_iter=50, tol=1e-6):
    """
    早期行使臨界価格をニュートン法でベクトル求解する（コール/プット共通）。
    収束済み・|df|<1e-12 の要素は更新を凍結し、旧スカラー実装の break/return と
    同じ停止挙動を再現する。クランプ:
      コール: S >= K*1.001
      プット: K*0.001 <= S <= K*0.999
    """
    S = S_init.copy()
    converged = np.zeros(S.shape, dtype=bool)
    exp_br = np.exp((b - r) * T)
    sqT = sigma * np.sqrt(T)

    for _ in range(max_iter):
        d1 = (np.log(S / K) + (b + 0.5 * sigma ** 2) * T) / sqT
        if is_call:
            bs = _bs_price_vec(S, K, T, b, r, sigma, True)
            early = exp_br * norm.cdf(d1)
            f = (S - K) - bs - (S / qparam) * (1.0 - early)
            d_bs_dS = exp_br * norm.cdf(d1)
            d_early_dS = exp_br * norm.pdf(d1) / (S * sqT)
            df = 1.0 - d_bs_dS - (1.0 / qparam) * (1.0 - early) + (S / qparam) * d_early_dS
        else:
            bs = _bs_price_vec(S, K, T, b, r, sigma, False)
            early = exp_br * norm.cdf(-d1)
            f = (K - S) - bs + (S / qparam) * (1.0 - early)
            d_early_dS = -exp_br * norm.pdf(d1) / (S * sqT)
            df = -1.0 + early + (1.0 / qparam) * (1.0 - early) - (S / qparam) * d_early_dS

        small_df = np.abs(df) < 1e-12
        step = np.where(small_df, 0.0, f / np.where(small_df, 1.0, df))
        S_new = S - step
        if is_call:
            S_new = np.maximum(S_new, K * 1.001)
        else:
            S_new = np.minimum(S_new, K * 0.999)
            S_new = np.maximum(S_new, K * 0.001)

        newly_conv = np.abs(S_new - S) < tol
        active = ~converged
        update_mask = active & ~small_df
        S = np.where(update_mask, S_new, S)
        # |df|<1e-12 は現値のまま凍結、更新後 tol 未満も凍結（スカラー return 相当）
        converged = converged | (active & small_df) | (update_mask & newly_conv)
        if converged.all():
            break

    return S


def _baw_price_given_critical_vec(S, K, T, b, r, sigma, q1, q2, S_crit, is_call):
    """
    事前計算済み臨界価格 S_crit を用いて BAW 価格を配列で返す（3点評価で再利用）。
    S はスカラー（スポット±ΔS）、その他は配列。S_crit=inf（無配当コール）は
    早期行使プレミアム=0 として純 BS 価格に帰着させる。
    """
    bs = _bs_price_vec(S, K, T, b, r, sigma, is_call)

    # inf を含むと A2/A1 が nan になるため、計算用にダミー値で置換し後で上書き
    inf_crit = np.isinf(S_crit)
    S_crit_safe = np.where(inf_crit, K, S_crit)

    sqT = sigma * np.sqrt(T)
    d1_crit = (np.log(S_crit_safe / K) + (b + 0.5 * sigma ** 2) * T) / sqT
    ratio = S / S_crit_safe

    # コール枝
    A2 = (S_crit_safe / q2) * (1.0 - np.exp((b - r) * T) * norm.cdf(d1_crit))
    call_val = np.where(S >= S_crit, np.maximum(S - K, 0.0), bs + A2 * ratio ** q2)
    call_val = np.where(inf_crit, bs, call_val)   # 無配当コール → 純BS

    # プット枝（S_crit に inf は来ない）
    A1 = -(S_crit_safe / q1) * (1.0 - np.exp((b - r) * T) * norm.cdf(-d1_crit))
    put_val = np.where(S <= S_crit, np.maximum(K - S, 0.0), bs + A1 * ratio ** q1)

    return np.where(is_call, call_val, put_val)


def baw_gamma_batch(S, K, T, r, q, sigma, is_call):
    """
    全コントラクトの BAW ガンマを数値二次微分で一括算出する。

      Γ = (V(S+ΔS) - 2·V(S) + V(S-ΔS)) / ΔS²   (ΔS = S × 0.001)

    Args:
        S (float):   スポット価格（全コントラクト共通スカラー）
        K, T, sigma: ストライク / 残存年数 / IV の numpy 配列
        r, q (float): 無リスク金利 / 配当利回り（銘柄共通スカラー）
        is_call (np.ndarray[bool]): コール=True / プット=False

    Returns:
        np.ndarray: ガンマ配列（非負、深いOTM・無効値は 0）
    """
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)

    if K.size == 0:
        return np.zeros(0, dtype=float)

    sigma = np.maximum(sigma, 0.001)
    T = np.maximum(T, 1.0 / 365)
    b = r - q
    dS = S * 0.001

    deep_otm = np.abs(np.log(S / K)) / (sigma * np.sqrt(T)) > 5.0

    q1, q2 = _baw_q_params_vec(T, r, q, sigma)

    # 臨界価格をコール/プット別に求解
    S_crit = np.empty(K.shape, dtype=float)
    call_mask = is_call
    put_mask = ~is_call

    if call_mask.any():
        if b >= r:
            # 配当なしコール: 早期行使は最適でない → 純BSガンマ（プレミアム=0）
            S_crit[call_mask] = np.inf
        else:
            S0 = np.maximum(
                K[call_mask] * (1.0 + sigma[call_mask] * np.sqrt(T[call_mask])),
                K[call_mask] * 1.001
            )
            S_crit[call_mask] = _newton_vec(
                S0, K[call_mask], T[call_mask], b, r, sigma[call_mask],
                q2[call_mask], is_call=True
            )

    if put_mask.any():
        S0 = K[put_mask] * np.maximum(0.5, 1.0 - sigma[put_mask] * np.sqrt(T[put_mask]))
        S0 = np.minimum(S0, K[put_mask] * 0.999)
        S0 = np.maximum(S0, K[put_mask] * 0.001)
        S_crit[put_mask] = _newton_vec(
            S0, K[put_mask], T[put_mask], b, r, sigma[put_mask],
            q1[put_mask], is_call=False
        )

    # 3 点評価（S_crit / q1 / q2 を共有）
    V_up = _baw_price_given_critical_vec(S + dS, K, T, b, r, sigma, q1, q2, S_crit, is_call)
    V_mid = _baw_price_given_critical_vec(S, K, T, b, r, sigma, q1, q2, S_crit, is_call)
    V_dn = _baw_price_given_critical_vec(S - dS, K, T, b, r, sigma, q1, q2, S_crit, is_call)

    gamma = (V_up - 2.0 * V_mid + V_dn) / (dS ** 2)
    gamma = np.maximum(gamma, 0.0)          # ガンマは非負
    gamma = np.where(deep_otm, 0.0, gamma)  # 深いOTMは 0
    return gamma


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

    # ── BAW ガンマ計算（全コントラクト一括） ──────────────────
    # 臨界価格 S* はコール/プット別に Newton 法でベクトル求解し、
    # 数値微分の 3 点評価で再利用することで計算量を削減している。
    chain['gamma'] = baw_gamma_batch(
        S=spot,
        K=chain['strike'].to_numpy(dtype=float),
        T=chain['T'].to_numpy(dtype=float),
        r=r,
        q=q,
        sigma=chain['impliedVolatility'].to_numpy(dtype=float),
        is_call=(chain['optionType'] == 'call').to_numpy(),
    )

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
