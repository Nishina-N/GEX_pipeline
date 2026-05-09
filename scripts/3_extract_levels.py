"""
3_extract_levels.py

ストライク別 Net GEX プロファイルから主要 GEX レベルを抽出する。

抽出対象:
  - HVL (Gamma Flip / Zero Gamma Level)
  - Call Resistance / Call Walls（正 Net GEX 上位N本）
  - Put Support / Put Walls（負 Net GEX 上位N本）
  - Transition Zone（PutSupport〜CallResistance の帯域）

集計タイプごとに独立して抽出:
  - total     : 全満期合算
  - short_term: DTE 0-7
  - long_term : 次の2月次SQ
"""

import os
import sys
import json
import pickle
import logging

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

DATA_FOLDER = "data"
GEX_DIR = os.path.join(DATA_FOLDER, "gex")
LEVELS_DIR = os.path.join(DATA_FOLDER, "levels")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "settings.json")
SCREENER_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "screener_config.json")
OI_SURGE_FILE = os.path.join(DATA_FOLDER, "symbols_oi_surge.json")


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────
# HVL (Gamma Flip)
# ─────────────────────────────────────────────────────────────

def find_hvl(gex_df, spot_price):
    """
    Net GEX が正→負（または負→正）に切り替わるストライクを線形補間で算出する。
    複数のゼロクロスが存在する場合、spot_price に最も近いものを返す。

    Returns:
        float or None
    """
    df = gex_df.sort_values('strike').reset_index(drop=True)
    strikes = df['strike'].values
    net_gex = df['netGEX'].values

    zero_crossings = []
    for i in range(len(net_gex) - 1):
        if net_gex[i] * net_gex[i + 1] < 0:
            s1, s2 = strikes[i], strikes[i + 1]
            g1, g2 = net_gex[i], net_gex[i + 1]
            zero_strike = s1 + (s2 - s1) * (-g1) / (g2 - g1)
            zero_crossings.append(zero_strike)

    if not zero_crossings:
        # ゼロクロスなし → Net GEX が 0 に最も近いストライクを返す
        closest_idx = np.argmin(np.abs(net_gex))
        return float(strikes[closest_idx])

    distances = [abs(zc - spot_price) for zc in zero_crossings]
    return float(zero_crossings[np.argmin(distances)])


# ─────────────────────────────────────────────────────────────
# Call Wall / Put Wall
# ─────────────────────────────────────────────────────────────

def find_walls(gex_df, top_n=3):
    """
    Call Wall（正 Net GEX 上位）と Put Wall（負 Net GEX 上位）を抽出する。

    仕様 §4.2-4.3:
      Call Resistance = argmax NETGEX(K) > 0  （上値抵抗）
      Put Support     = argmin NETGEX(K) < 0  （下値支持）

    Returns:
        dict: {
            'callWalls': [{'strike': float, 'netGEX': float}, ...],  # 降順
            'putWalls':  [{'strike': float, 'netGEX': float}, ...],  # 昇順（絶対値降順）
        }
    """
    df = gex_df.copy()

    positive = df[df['netGEX'] > 0].nlargest(top_n, 'netGEX')
    call_walls = [
        {'strike': float(row['strike']), 'netGEX': float(row['netGEX'])}
        for _, row in positive.iterrows()
    ]

    negative = df[df['netGEX'] < 0].nsmallest(top_n, 'netGEX')
    put_walls = [
        {'strike': float(row['strike']), 'netGEX': float(row['netGEX'])}
        for _, row in negative.iterrows()
    ]

    return {'callWalls': call_walls, 'putWalls': put_walls}


# ─────────────────────────────────────────────────────────────
# 1つの GEX DataFrame からレベルセットを抽出するヘルパー
# ─────────────────────────────────────────────────────────────

def extract_level_set(gex_df, spot_price, top_n=3):
    """
    1つの集計 GEX DataFrame (gex_by_strike / gex_short_term / gex_long_term)
    から HVL・Call/Put Wall・Transition Zone を抽出してまとめて返す。

    Returns:
        dict or None (gex_df が None または空の場合)
    """
    if gex_df is None or gex_df.empty:
        return None

    hvl = find_hvl(gex_df, spot_price)
    walls = find_walls(gex_df, top_n=top_n)

    call_wall = walls['callWalls'][0]['strike'] if walls['callWalls'] else None
    put_wall = walls['putWalls'][0]['strike'] if walls['putWalls'] else None

    # Transition Zone: Put Support〜Call Resistance の帯域
    transition_zone = None
    if put_wall is not None and call_wall is not None:
        transition_zone = {
            'lower': put_wall,
            'upper': call_wall
        }

    # ゾーン判定（spot と HVL の位置関係）
    sentiment = 'neutral'
    if hvl is not None:
        sentiment = 'positive_gamma' if spot_price > hvl else 'negative_gamma'

    return {
        'hvl': hvl,
        'callWall': call_wall,
        'putWall': put_wall,
        'callWalls': walls['callWalls'],
        'putWalls': walls['putWalls'],
        'transition_zone': transition_zone,
        'sentiment': sentiment,
    }


# ─────────────────────────────────────────────────────────────
# GEX プロファイル（ヒストグラム用）
# ─────────────────────────────────────────────────────────────

def build_profile(gex_df, spot_price, range_pct=0.20):
    """
    スポット価格 ± range_pct 以内のストライクを抽出してプロファイルリストを返す。
    """
    if gex_df is None or gex_df.empty:
        return []

    price_range = spot_price * range_pct
    filtered = gex_df[
        (gex_df['strike'] >= spot_price - price_range) &
        (gex_df['strike'] <= spot_price + price_range)
    ]

    return [
        {
            'strike': float(row['strike']),
            'callGEX': float(row['callGEX']),
            'putGEX': float(row['putGEX']),
            'netGEX': float(row['netGEX']),
        }
        for _, row in filtered.iterrows()
    ]


# ─────────────────────────────────────────────────────────────
# メイン抽出
# ─────────────────────────────────────────────────────────────

def extract_levels_for_symbol(gex_data, config):
    """
    1銘柄の GEX データから全レベルを抽出する。

    出力 JSON 構造:
    {
      "ticker": str,
      "date": str,
      "spotPrice": float,
      "totalGEX": float,
      "sentiment": str,          # 全体 GEX 基準
      "levels": {
        # 全満期合算
        "hvl": float,
        "callWall": float,
        "putWall": float,
        "callWalls": [...],
        "putWalls": [...],
        "transition_zone": {"lower": float, "upper": float},

        # 短期・長期
        "short_term": { hvl, callWall, putWall, callWalls, putWalls,
                        transition_zone, sentiment },
        "long_term":  { hvl, callWall, putWall, callWalls, putWalls,
                        transition_zone, sentiment },
      },
      "profile": {
        "total":      [...],   # 全満期プロファイル
        "short_term": [...],
        "long_term":  [...],
      },
      "zeroDTE": {...} or null,
      "expirationInfo": {
        "shortTermExpirations": [...],
        "longTermExpirations": [...],
      },
      "metadata": {...}
    }
    """
    symbol = gex_data['symbol']
    spot = gex_data['spot_price']
    date = gex_data['date']
    total_gex = gex_data['total_gex']

    top_n = config.get('top_n_levels', 3)

    # ── 各集計タイプのレベル抽出 ──────────────────────────────
    levels_total = extract_level_set(gex_data['gex_by_strike'], spot, top_n)
    levels_st = extract_level_set(gex_data.get('gex_short_term'), spot, top_n)
    levels_lt = extract_level_set(gex_data.get('gex_long_term'), spot, top_n)

    if levels_total is None:
        logging.warning(f"[{symbol}] No GEX data to extract levels from")
        return None

    # ── GEX プロファイル ──────────────────────────────────────
    profile_total = build_profile(gex_data['gex_by_strike'], spot)
    profile_st = build_profile(gex_data.get('gex_short_term'), spot)
    profile_lt = build_profile(gex_data.get('gex_long_term'), spot)

    # ── 0DTE サマリ ───────────────────────────────────────────
    gex_0dte = gex_data.get('gex_0dte')
    zero_dte_info = None
    if gex_0dte is not None and not gex_0dte.empty:
        dte_total = float(gex_0dte['netGEX'].sum())
        dte_top = gex_0dte.reindex(gex_0dte['netGEX'].abs().nlargest(top_n).index)
        zero_dte_info = {
            'totalGEX': dte_total,
            'topStrikes': [
                {'strike': float(row['strike']), 'netGEX': float(row['netGEX'])}
                for _, row in dte_top.iterrows()
            ],
        }

    # ── GEX有効性・組み合わせ分類 ────────────────────────────────
    abs_total_gex = abs(float(total_gex))
    if abs_total_gex >= 1_000_000_000:
        gex_applicable = "high"
    elif abs_total_gex >= 200_000_000:
        gex_applicable = "moderate"
    elif abs_total_gex >= 50_000_000:
        gex_applicable = "low"
    else:
        gex_applicable = "insufficient"

    # totalGEXの符号 × sentimentで1〜4に分類
    # 1: +totalGEX × positive_gamma (安定レンジ)
    # 2: -totalGEX × positive_gamma (混合)
    # 3: +totalGEX × negative_gamma (急騰候補 HVL回復型)
    # 4: -totalGEX × negative_gamma (最強急騰・急落候補)
    total_gex_positive = float(total_gex) >= 0
    sentiment_positive = levels_total['sentiment'] == 'positive_gamma'
    if total_gex_positive and sentiment_positive:
        gex_combination = 1
    elif not total_gex_positive and sentiment_positive:
        gex_combination = 2
    elif total_gex_positive and not sentiment_positive:
        gex_combination = 3
    else:
        gex_combination = 4

    result = {
        'ticker': symbol,
        'date': date,
        'spotPrice': float(spot),
        'totalGEX': float(total_gex),
        'sentiment': levels_total['sentiment'],
        'gex_applicable': gex_applicable,
        'gex_combination': gex_combination,
        'levels': {
            # 全満期合算
            'hvl': levels_total['hvl'],
            'callWall': levels_total['callWall'],
            'putWall': levels_total['putWall'],
            'callWalls': levels_total['callWalls'],
            'putWalls': levels_total['putWalls'],
            'transition_zone': levels_total['transition_zone'],
            # 短期・長期
            'short_term': levels_st,
            'long_term': levels_lt,
        },
        'profile': {
            'total': profile_total,
            'short_term': profile_st,
            'long_term': profile_lt,
        },
        'zeroDTE': zero_dte_info,
        'expirationInfo': {
            'shortTermExpirations': gex_data.get('short_term_expirations', []),
            'longTermExpirations': gex_data.get('long_term_expirations', []),
        },
        'metadata': {
            'expirations_used': gex_data.get('expirations_used', 0),
            'total_contracts': gex_data.get('total_contracts', 0),
            'profile_range_pct': 20,
            'calculation_time': pd.Timestamp.now().isoformat(),
        },
    }

    # ── ログ ──────────────────────────────────────────────────
    def _fmt(level_set, label):
        if level_set is None:
            return f"{label}: (データなし)"
        hvl = level_set['hvl']
        cw = level_set['callWall']
        pw = level_set['putWall']
        sent = level_set['sentiment']
        hvl_s = f"{hvl:.2f}" if hvl is not None else 'N/A'
        cw_s  = f"{cw:.1f}"  if cw  is not None else 'N/A'
        pw_s  = f"{pw:.1f}"  if pw  is not None else 'N/A'
        return f"{label}: HVL={hvl_s}, Call={cw_s}, Put={pw_s}, {sent}"

    logging.info(f"[{symbol}] " + _fmt(levels_total, "Total"))
    logging.info(f"[{symbol}] " + _fmt(levels_st, "ShortTerm"))
    logging.info(f"[{symbol}] " + _fmt(levels_lt, "LongTerm"))

    return result


def _write_filter_result_to_levels(symbol: str, passed: bool, reason: str) -> None:
    """levels JSON にガンマフィルタ結果を書き戻す。"""
    level_path = os.path.join(LEVELS_DIR, f"{symbol}.json")
    if not os.path.exists(level_path):
        return
    try:
        with open(level_path, 'r') as f:
            level_data = json.load(f)
        level_data["gamma_filter_passed"] = passed
        level_data["gamma_filter_reason"] = reason
        with open(level_path, 'w') as f:
            json.dump(level_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.warning(f"[GammaFilter] [{symbol}] levels JSON 更新失敗: {e}")


def filter_oi_surge_by_gamma() -> None:
    """
    data/symbols_oi_surge.json の各銘柄について
    data/levels/{symbol}.json の GEX 情報を確認し、急騰不適切な銘柄を除外する。

    【ブラックリスト方式】
    sentimentで絞り込むのではなく、以下の「除外条件」に該当する銘柄のみを除外する:
      1. GEX不十分: |totalGEX| < min_total_gex_usd（GEX分析自体が無効）
      2. ブレイクダウン局面: spot < put_wall（急落方向の銘柄は除外）
    これにより negative_gamma（パターンA: プレブレイクアウト）も
    positive_gamma（パターンB: ブレイクアウト後モメンタム）も両方保持できる。

    出力フィールド追加:
      - gamma_sentiment  : sentiment の値
      - surge_pattern    : "pre_breakout"（negative_gamma）or
                           "breakout_momentum"（positive_gamma）or None
      - gex_applicable   : "high" / "moderate" / "low" / "insufficient"

    - always_include 銘柄（SPY, QQQ 等）は gamma フィルタをスキップ（設定で変更可能）
    - GEX データなしの銘柄は no_data_behavior に従い除外 or 保持
    """
    # screener_config.json を読み込む
    try:
        with open(SCREENER_CONFIG_PATH, 'r') as f:
            screener_cfg = json.load(f)
    except Exception as e:
        logging.error(f"[GammaFilter] screener_config.json の読み込みに失敗: {e}")
        return

    gamma_filter_cfg = screener_cfg.get("gamma_filter", {})
    if not gamma_filter_cfg.get("enabled", True):
        logging.info("[GammaFilter] gamma_filter が無効化されています。スキップします。")
        return

    always_include = screener_cfg.get("output", {}).get("always_include", [])
    apply_to_always_include = gamma_filter_cfg.get("apply_to_always_include", False)
    no_data_behavior = gamma_filter_cfg.get("no_data_behavior", "exclude")  # "exclude" or "include"
    min_total_gex_usd = gamma_filter_cfg.get("min_total_gex_usd", 50_000_000)
    exclude_breakdown = gamma_filter_cfg.get("exclude_breakdown", True)

    # symbols_oi_surge.json を読み込む
    if not os.path.exists(OI_SURGE_FILE):
        logging.warning(f"[GammaFilter] {OI_SURGE_FILE} が見つかりません。スキップします。")
        return

    try:
        with open(OI_SURGE_FILE, 'r') as f:
            surge_data = json.load(f)
    except Exception as e:
        logging.error(f"[GammaFilter] {OI_SURGE_FILE} の読み込みに失敗: {e}")
        return

    original_symbols = surge_data.get("symbols", [])
    results = surge_data.get("screening_results", [])

    filtered_symbols = []
    removed_symbols = []

    for entry in results:
        symbol = entry.get("symbol")
        if symbol is None:
            continue

        # always_include かつ apply_to_always_include=False の場合はフィルタをスキップ
        skip_filter = (symbol in always_include and not apply_to_always_include)

        # data/levels/{symbol}.json から GEX 情報を取得
        level_path = os.path.join(LEVELS_DIR, f"{symbol}.json")
        gamma_sentiment = None
        total_gex = None
        spot_price = None
        put_wall = None
        gex_applicable = None

        if os.path.exists(level_path):
            try:
                with open(level_path, 'r') as f:
                    level_data = json.load(f)
                gamma_sentiment = level_data.get("sentiment")
                total_gex = level_data.get("totalGEX")
                spot_price = level_data.get("spotPrice")
                put_wall = level_data.get("levels", {}).get("putWall")
                gex_applicable = level_data.get("gex_applicable")
            except Exception as e:
                logging.warning(f"[GammaFilter] [{symbol}] levels JSON の読み込みに失敗: {e}")

        # surge_pattern の決定
        if gamma_sentiment == 'negative_gamma':
            surge_pattern = "pre_breakout"         # HVL下 → ブレイクアウト前夜
        elif gamma_sentiment == 'positive_gamma':
            surge_pattern = "breakout_momentum"    # HVL上 → ブレイクアウト後モメンタム
        else:
            surge_pattern = None

        # screening_results エントリにフィールドを追加（可視性・監査用）
        entry["gamma_sentiment"] = gamma_sentiment
        entry["surge_pattern"] = surge_pattern
        entry["gex_applicable"] = gex_applicable

        # symbols リストに含まれていない銘柄は判定対象外
        if symbol not in original_symbols:
            continue

        if skip_filter:
            filtered_symbols.append(symbol)
            logging.info(
                f"[GammaFilter] [{symbol}] always_include のためフィルタをスキップ"
                f"（gamma: {gamma_sentiment}, surge_pattern: {surge_pattern}）"
            )
            _write_filter_result_to_levels(symbol, passed=True, reason="always_include_skip")
            continue

        # GEX データなし → no_data_behavior に従う
        if gamma_sentiment is None:
            if no_data_behavior == "include":
                filtered_symbols.append(symbol)
                logging.info(f"[GammaFilter] [{symbol}] GEX データなし → 保持（no_data_behavior=include）")
                _write_filter_result_to_levels(symbol, passed=True, reason="no_data_include")
            else:
                removed_symbols.append(symbol)
                logging.info(f"[GammaFilter] [{symbol}] GEX データなし → 除外（no_data_behavior=exclude）")
                _write_filter_result_to_levels(symbol, passed=False, reason="no_data_exclude")
            continue

        # ── ブラックリスト方式の除外判定 ──────────────────────────
        exclude = False
        exclude_reason = ""

        # 除外条件① GEX不十分（分析無効）
        if total_gex is not None and abs(total_gex) < min_total_gex_usd:
            exclude = True
            exclude_reason = (
                f"GEX insufficient: |{total_gex:.0f}| < {min_total_gex_usd:.0f}"
            )

        # 除外条件② ブレイクダウン局面（Spot < Put Wall）
        elif exclude_breakdown and spot_price is not None and put_wall is not None:
            if spot_price < put_wall:
                exclude = True
                exclude_reason = (
                    f"breakdown: spot({spot_price:.1f}) < putWall({put_wall:.1f})"
                )

        if exclude:
            removed_symbols.append(symbol)
            logging.info(f"[GammaFilter] [{symbol}] 除外 → {exclude_reason}")
            _write_filter_result_to_levels(symbol, passed=False, reason=exclude_reason)
        else:
            filtered_symbols.append(symbol)
            logging.info(
                f"[GammaFilter] [{symbol}] 保持 "
                f"（surge_pattern: {surge_pattern}, gex_applicable: {gex_applicable}）"
            )
            _write_filter_result_to_levels(symbol, passed=True, reason="gamma_filter_passed")

    # ETF銘柄は OI スクリーニング対象外のため screening_results に含まれず
    # メインループでは処理されない。ここで明示的に自動保持する。
    etf_symbols = screener_cfg.get("output", {}).get("etf_symbols", [])
    for symbol in original_symbols:
        if (symbol in etf_symbols
                and symbol not in filtered_symbols
                and symbol not in removed_symbols):
            filtered_symbols.append(symbol)
            logging.info(
                f"[GammaFilter] [{symbol}] ETFシンボル（OIスクリーニング対象外）→ 自動保持"
            )
            _write_filter_result_to_levels(symbol, passed=True, reason="etf_auto_retain")

    # 元の順序を維持しながらフィルタ済みリストを再構築
    filtered_symbols_ordered = [s for s in original_symbols if s in filtered_symbols]

    surge_data["symbols"] = filtered_symbols_ordered
    surge_data["gamma_filter_applied"] = True
    surge_data["gamma_filter_removed"] = removed_symbols

    try:
        with open(OI_SURGE_FILE, 'w') as f:
            json.dump(surge_data, f, indent=2, ensure_ascii=False)
        logging.info(
            f"[GammaFilter] 完了: {len(filtered_symbols_ordered)} 銘柄保持 / "
            f"{len(removed_symbols)} 銘柄除外: {removed_symbols}"
        )
    except Exception as e:
        logging.error(f"[GammaFilter] {OI_SURGE_FILE} の保存に失敗: {e}")


def main():
    config = load_config()
    os.makedirs(LEVELS_DIR, exist_ok=True)

    logging.info("=" * 60)
    logging.info("EXTRACT GEX LEVELS")
    logging.info("=" * 60)

    if not os.path.exists(GEX_DIR):
        logging.error(f"GEX data directory not found: {GEX_DIR}")
        return False

    pkl_files = [f for f in os.listdir(GEX_DIR) if f.endswith('.pkl')]
    if not pkl_files:
        logging.error("No GEX data files found")
        return False

    success_count = 0
    fail_count = 0

    for pkl_file in pkl_files:
        symbol = pkl_file.replace('.pkl', '')

        try:
            with open(os.path.join(GEX_DIR, pkl_file), 'rb') as f:
                gex_data = pickle.load(f)

            result = extract_levels_for_symbol(gex_data, config)

            if result:
                output_path = os.path.join(LEVELS_DIR, f"{symbol}.json")
                with open(output_path, 'w') as f:
                    json.dump(result, f, indent=2)
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

    # gamma フィルタ: symbols_oi_surge.json を positive_gamma 銘柄のみに絞り込む
    filter_oi_surge_by_gamma()

    return success_count > 0


if __name__ == "__main__":
    if main():
        sys.exit(0)
    else:
        sys.exit(1)
