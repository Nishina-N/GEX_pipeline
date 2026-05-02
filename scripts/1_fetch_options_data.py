"""
1_fetch_options_data.py

yfinance からオプションチェーンデータを取得し、ローカルに一時保存する。
"""
import os
import sys
import json
import time
import pickle
import logging
import argparse

import yfinance as yf
import pandas as pd

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
            from datetime import datetime, timedelta
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

            return {
                'symbol': symbol,
                'spot_price': spot_price,
                'chain': full_chain,
                'expirations': filtered_expirations
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


def main(symbols=None):
    config = load_config()

    if symbols is None:
        symbols = config['target_symbols']

    max_expiry_days = config.get('max_expiry_days', 90)
    delay      = config['rate_limit']['delay_between_symbols']
    max_retries = config['rate_limit']['max_retries']
    backoff    = config['rate_limit']['backoff_factor']
    today_str  = pd.Timestamp.now().strftime('%Y-%m-%d')

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
    parser = argparse.ArgumentParser(description="Fetch options data from yfinance")
    parser.add_argument(
        '--symbols', nargs='+', default=None,
        help='Override target symbols (e.g., --symbols SPY AAPL TSLA)'
    )
    args = parser.parse_args()

    if main(symbols=args.symbols):
        sys.exit(0)
    else:
        sys.exit(1)
