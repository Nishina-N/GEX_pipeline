"""
1_fetch_options_data.py

オプションチェーンを取得し、ローカルに一時保存する。

取得元:
  1. Cboe の遅延データ（cdn.cboe.com, 約15分遅れ, 1日1回の取得で十分）
     - ET 0時を過ぎても前日の建玉(OI)が残る
  2. 取れなかった銘柄だけ yfinance で取る（予備）
     - yfinance は ET 0時を過ぎると OI が 0 になるため、OI>0 の行が少なすぎる結果は捨てる
"""
import re
from datetime import datetime, timedelta
import os
import sys
import json
import time
import pickle
import logging
import argparse

import yfinance as yf
import pandas as pd
import requests

from market_calendar import get_effective_market_date, set_pipeline_date

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

DATA_FOLDER = "data"
OPTIONS_DIR  = os.path.join(DATA_FOLDER, "options")
IV_HIST_DIR  = os.path.join(DATA_FOLDER, "iv_history")   # σ_20MA 計算用の日次スナップショット

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "settings.json")


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)



CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
CBOE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.cboe.com/",
}
MIN_OI_RATIO = 0.2   # OI>0 の行がこれ未満なら「壊れたデータ」とみなす


def fetch_options_cboe(symbol, max_expiry_days=90, max_retries=3, backoff_factor=2):
    """Cboe の遅延データから 1 銘柄の全満期を取得する。返す形は yfinance 版と同じ。"""
    pat = re.compile(r"^" + re.escape(symbol) + r"(\d{6})([CP])(\d{8})$")
    for attempt in range(max_retries):
        try:
            r = requests.get(CBOE_URL.format(symbol=symbol), headers=CBOE_HEADERS, timeout=60)
            if r.status_code == 404:
                logging.warning(f"[{symbol}] Cboe: no data (404)")
                return None
            r.raise_for_status()
            js = r.json()
            data = js["data"]
            # 引け後は close が当日の終値。current_price は時間外を含むので使わない
            spot_price = float(data.get("close") or data.get("current_price") or 0)
            if spot_price <= 0:
                logging.warning(f"[{symbol}] Cboe: no spot price")
                return None

            cutoff = datetime.now() + timedelta(days=max_expiry_days)
            rows = []
            for o in data.get("options", []):
                m = pat.match(o.get("option", ""))
                if not m:
                    continue   # 調整後オプション等（別ルート）は除外
                exp = datetime.strptime(m.group(1), "%y%m%d")
                if exp > cutoff:
                    continue
                strike = int(m.group(3)) / 1000.0
                is_call = m.group(2) == "C"
                rows.append({
                    "strike": strike,
                    "expiration": exp.strftime("%Y-%m-%d"),
                    "optionType": "call" if is_call else "put",
                    "openInterest": int(o.get("open_interest") or 0),
                    "impliedVolatility": float(o.get("iv") or 0),
                    "lastPrice": float(o.get("last_trade_price") or 0),
                    "volume": float(o.get("volume") or 0),
                    "bid": float(o.get("bid") or 0),
                    "ask": float(o.get("ask") or 0),
                    "inTheMoney": (spot_price > strike) if is_call else (spot_price < strike),
                })
            if not rows:
                logging.warning(f"[{symbol}] Cboe: no contracts")
                return None
            chain = pd.DataFrame(rows)
            exps = sorted(chain["expiration"].unique().tolist())
            oi_ratio = (chain["openInterest"] > 0).mean()
            logging.info(
                f"[{symbol}] Cboe ({js.get('timestamp')} ET): Spot ${spot_price:.2f}, "
                f"{len(chain)} contracts across {len(exps)} expirations, OI>0 {oi_ratio:.0%}"
            )
            if oi_ratio < MIN_OI_RATIO:
                logging.warning(f"[{symbol}] Cboe: OI>0 が {oi_ratio:.0%} しかない。使わない")
                return None
            return {"symbol": symbol, "spot_price": spot_price, "chain": chain,
                    "expirations": exps, "source": "cboe"}
        except Exception as e:
            wait_time = backoff_factor ** attempt
            logging.error(f"[{symbol}] Cboe attempt {attempt + 1}/{max_retries} failed: {e}")
            time.sleep(wait_time)
    return None

def fetch_options_for_symbol(symbol, max_expiry_days=90, max_retries=3, backoff_factor=2):
    """
    1銘柄のオプションチェーンを全満期日分取得する。

    Returns:
        dict: {
            'symbol': str,
            'spot_price': float,
            'chains': list of DataFrames (call/put merged per expiry)
        }
        or None if failed
    """
    for attempt in range(max_retries):
        try:
            ticker = yf.Ticker(symbol)

            # スポット価格の取得
            hist = ticker.history(period="1d")
            if hist.empty:
                logging.warning(f"[{symbol}] No price data available")
                return None
            spot_price = float(hist['Close'].iloc[-1])

            # 満期日一覧の取得
            try:
                expirations = ticker.options
            except Exception:
                logging.warning(f"[{symbol}] No options available")
                return None

            if not expirations:
                logging.warning(f"[{symbol}] No options expirations found")
                return None

            # 満期日のフィルタリング（max_expiry_days 以内）
            cutoff_date = datetime.now() + timedelta(days=max_expiry_days)
            filtered_expirations = []
            for exp in expirations:
                exp_date = datetime.strptime(exp, "%Y-%m-%d")
                if exp_date <= cutoff_date:
                    filtered_expirations.append(exp)

            if not filtered_expirations:
                logging.warning(f"[{symbol}] No expirations within {max_expiry_days} days")
                return None

            logging.info(f"[{symbol}] Spot: ${spot_price:.2f}, Expirations: {len(filtered_expirations)}")

            # 各満期日ごとにオプションチェーン取得
            all_chains = []
            for exp in filtered_expirations:
                try:
                    chain = ticker.option_chain(exp)

                    # Callデータ
                    calls = chain.calls.copy()
                    calls['optionType'] = 'call'
                    calls['expiration'] = exp

                    # Putデータ
                    puts = chain.puts.copy()
                    puts['optionType'] = 'put'
                    puts['expiration'] = exp

                    combined = pd.concat([calls, puts], ignore_index=True)
                    all_chains.append(combined)

                except Exception as e:
                    logging.warning(f"[{symbol}] Failed to fetch chain for {exp}: {e}")
                    continue

            if not all_chains:
                logging.warning(f"[{symbol}] No valid option chains retrieved")
                return None

            full_chain = pd.concat(all_chains, ignore_index=True)

            # 必要カラムのみ保持
            keep_cols = [
                'strike', 'expiration', 'optionType',
                'openInterest', 'impliedVolatility', 'lastPrice',
                'volume', 'bid', 'ask', 'inTheMoney'
            ]
            available_cols = [c for c in keep_cols if c in full_chain.columns]
            full_chain = full_chain[available_cols]

            # openInterest の欠損を 0 に
            if 'openInterest' in full_chain.columns:
                full_chain['openInterest'] = full_chain['openInterest'].fillna(0).astype(int)

            logging.info(
                f"[{symbol}] Fetched {len(full_chain)} option contracts "
                f"across {len(filtered_expirations)} expirations"
            )

            oi_ratio = (full_chain['openInterest'] > 0).mean() if 'openInterest' in full_chain.columns else 0
            if oi_ratio < MIN_OI_RATIO:
                logging.warning(
                    f"[{symbol}] yfinance: OI>0 が {oi_ratio:.0%} しかない"
                    "（ET 0時過ぎの可能性）。使わない"
                )
                return None

            return {
                'symbol': symbol,
                'spot_price': spot_price,
                'chain': full_chain,
                'expirations': filtered_expirations,
                'source': 'yfinance'
            }

        except Exception as e:
            wait_time = backoff_factor ** attempt
            logging.error(
                f"[{symbol}] Attempt {attempt + 1}/{max_retries} failed: {e}. "
                f"Retrying in {wait_time}s..."
            )
            time.sleep(wait_time)

    logging.error(f"[{symbol}] All {max_retries} attempts failed")
    return None


def save_iv_snapshot(result, today_str):
    """
    IV の日次スナップショットを data/iv_history/{symbol}/{YYYY-MM-DD}.pkl に保存する。

    保存内容: ストライク・満期・オプション種別ごとの impliedVolatility
    用途: 将来の σ_20MA 計算（GEX バーの色判定に使用）

    保存日数が config の gex_retention_days を超えた古いスナップショットは削除する。
    """
    symbol    = result['symbol']
    chain     = result['chain']
    retention = 30   # デフォルト保持日数

    sym_dir = os.path.join(IV_HIST_DIR, symbol)
    os.makedirs(sym_dir, exist_ok=True)

    # 保存内容: strike / expiration / optionType / impliedVolatility のみ
    iv_cols = [c for c in ['strike', 'expiration', 'optionType', 'impliedVolatility']
               if c in chain.columns]
    iv_snapshot = chain[iv_cols].copy()
    iv_snapshot['date'] = today_str

    snap_path = os.path.join(sym_dir, f"{today_str}.pkl")
    with open(snap_path, 'wb') as f:
        pickle.dump(iv_snapshot, f)
    logging.info(f"[{symbol}] IV snapshot saved: {snap_path}")

    # 古いスナップショットを削除
    cutoff = (pd.Timestamp(today_str) - pd.Timedelta(days=retention)).strftime('%Y-%m-%d')
    for fname in os.listdir(sym_dir):
        if fname.endswith('.pkl') and fname.replace('.pkl', '') < cutoff:
            os.remove(os.path.join(sym_dir, fname))
            logging.debug(f"[{symbol}] Removed old IV snapshot: {fname}")


def main(symbols=None, symbols_from=None):
    config = load_config()

    if symbols_from is not None:
        with open(symbols_from, "r", encoding="utf-8") as f:
            data = json.load(f)
        symbols = data["symbols"]
        logging.info(f"Loaded {len(symbols)} symbols from {symbols_from}")
    elif symbols is None:
        symbols = config['target_symbols']

    max_expiry_days = config.get('max_expiry_days', 90)
    delay      = config['rate_limit']['delay_between_symbols']
    max_retries = config['rate_limit']['max_retries']
    backoff    = config['rate_limit']['backoff_factor']
    # 有効な市場営業日を決定してファイルに書き込む（Step 2〜7 はここから読む）
    today_str = get_effective_market_date()
    set_pipeline_date(today_str)
    logging.info(f"Effective market date (pipeline date): {today_str}")

    os.makedirs(OPTIONS_DIR, exist_ok=True)
    os.makedirs(IV_HIST_DIR, exist_ok=True)

    logging.info("=" * 60)
    logging.info("FETCH OPTIONS DATA")
    logging.info(f"Symbols: {symbols}")
    logging.info(f"Max expiry: {max_expiry_days} days")
    logging.info("=" * 60)

    success_count = 0
    fail_count    = 0

    for i, symbol in enumerate(symbols):
        logging.info(f"\n[{i + 1}/{len(symbols)}] Processing {symbol}...")

        result = fetch_options_cboe(
            symbol,
            max_expiry_days=max_expiry_days,
            max_retries=max_retries,
            backoff_factor=backoff
        )
        if result is None:
            logging.info(f"[{symbol}] Cboe で取れなかったので yfinance で取る")
            result = fetch_options_for_symbol(
                symbol,
                max_expiry_days=max_expiry_days,
                max_retries=max_retries,
                backoff_factor=backoff
            )

        if result:
            # オプションチェーン本体を保存
            output_path = os.path.join(OPTIONS_DIR, f"{symbol}.pkl")
            with open(output_path, 'wb') as f:
                pickle.dump(result, f)
            success_count += 1
            logging.info(f"[{symbol}] Saved to {output_path}")

            # IV 日次スナップショットを保存（σ_20MA 計算用）
            try:
                save_iv_snapshot(result, today_str)
            except Exception as e:
                logging.warning(f"[{symbol}] IV snapshot failed: {e}")
        else:
            fail_count += 1
            logging.warning(f"[{symbol}] Skipped (no data)")

        # レート制限対策
        if i < len(symbols) - 1:
            time.sleep(delay)

    logging.info("=" * 60)
    logging.info(f"Success: {success_count}, Failed: {fail_count}")
    logging.info("=" * 60)

    return success_count > 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch options data (Cboe, fallback yfinance)")
    parser.add_argument(
        '--symbols', nargs='+', default=None,
        help='Override target symbols (e.g., --symbols SPY AAPL TSLA)'
    )
    parser.add_argument(
        '--symbols-from', default=None,
        help='Path to JSON file with symbol list (e.g., data/symbols_oi_surge.json)'
    )
    args = parser.parse_args()

    if main(symbols=args.symbols, symbols_from=args.symbols_from):
        sys.exit(0)
    else:
        sys.exit(1)
