"""
0_screen_oi_surge.py

S&P500 / NASDAQ100 / Russell2000 ユニバースから
オプションOI（Open Interest）の前日比急増銘柄をスクリーニングし、
強気スコアで上位銘柄を既存GEXパイプラインへ渡す。

フロー:
  1. ユニバース取得（3ソース合成）
  2. 時価総額フィルタ（$2B以上）
  3. 前日OIキャッシュをR2から取得
  4. 本日OIスナップショットを並列取得
  5. OI変化率ゲートフィルタ（+20%以上かつ1000枚以上）
  6. 強気スコア計算・ランキング
  7. 出力: data/symbols_oi_surge.json
  8. 当日OIキャッシュをR2に保存（翌日用）

環境変数（.env または GitHub Secrets）:
  R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME
"""

import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from io import StringIO

import pandas as pd
import yfinance as yf
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

SCREENER_CONFIG_PATH = Path("config/screener_config.json")
OUTPUT_PATH          = Path("data/symbols_oi_surge.json")
DEFAULT_SYMBOLS      = ["SPY", "QQQ", "SMH", "DIA", "IWM", "NVDA", "AAPL", "TSLA"]


# ─── 設定読み込み ─────────────────────────────────────────────────────────

def load_screener_config() -> dict:
    if not SCREENER_CONFIG_PATH.exists():
        raise FileNotFoundError(f"Screener config not found: {SCREENER_CONFIG_PATH}")
    with open(SCREENER_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


# ─── ユニバース取得 ───────────────────────────────────────────────────────

def get_sp500_symbols() -> list[str]:
    """S&P500構成銘柄をWikipediaから取得"""
    tables = pd.read_html(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        attrs={"id": "constituents"}
    )
    symbols = tables[0]["Symbol"].tolist()
    # yfinance互換: BRK.B → BRK-B
    return [s.replace(".", "-") for s in symbols]


def get_nasdaq100_symbols() -> list[str]:
    """NASDAQ100構成銘柄をWikipediaから取得"""
    tables = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")
    # "Ticker" カラムを持つテーブルを探す
    for tbl in tables:
        cols = [str(c).strip() for c in tbl.columns]
        if "Ticker" in cols:
            return tbl["Ticker"].dropna().tolist()
    raise ValueError("NASDAQ100 table with 'Ticker' column not found on Wikipedia")


def get_russell2000_symbols() -> list[str]:
    """iShares IWM構成銘柄CSVからRussell2000銘柄を取得"""
    url = (
        "https://www.ishares.com/us/products/239710/"
        "ishares-russell-2000-etf/1467271812596.ajax"
        "?fileType=csv&fileName=IWM_holdings&dataType=fund"
    )
    resp = pd.read_csv(url, skiprows=9, on_bad_lines='skip')

    # ヘッダー行を自動検出
    if "Ticker" not in resp.columns:
        # "Ticker" という文字列が含まれる行を再検索
        raw = pd.read_csv(url, header=None, on_bad_lines='skip')
        header_row = None
        for i, row in raw.iterrows():
            if row.astype(str).str.contains("Ticker").any():
                header_row = i
                break
        if header_row is None:
            raise ValueError("IWM CSV: 'Ticker' column not found")
        resp = pd.read_csv(url, skiprows=header_row, on_bad_lines='skip')

    tickers = resp["Ticker"].dropna().astype(str).tolist()
    # キャッシュ行・空行を除外
    return [t.strip() for t in tickers if t.strip() and t.strip() != "-" and len(t.strip()) <= 6]


def build_universe(config: dict) -> list[str]:
    """3ソースからユニバースを構築。部分失敗は許容。"""
    symbols_set: set[str] = set()

    sources = [
        ("sp500",     "include_sp500",      get_sp500_symbols),
        ("nasdaq100", "include_nasdaq100",   get_nasdaq100_symbols),
        ("russell2000","include_russell2000", get_russell2000_symbols),
    ]

    for name, key, getter in sources:
        if not config["universe"].get(key, True):
            logging.info(f"[Universe] {name} skipped (disabled in config)")
            continue
        try:
            result = getter()
            logging.info(f"[Universe] {name}: {len(result)} symbols")
            symbols_set.update(result)
        except Exception as e:
            logging.warning(f"[Universe] {name} failed: {e}")

    if not symbols_set:
        logging.error("[Universe] All sources failed. Using DEFAULT_SYMBOLS.")
        return list(DEFAULT_SYMBOLS)

    exclude = set(config["universe"].get("exclude_symbols", []))
    filtered = sorted(symbols_set - exclude)
    logging.info(f"[Universe] Total unique symbols: {len(filtered)}")
    return filtered


# ─── 時価総額フィルタ ─────────────────────────────────────────────────────

def _fetch_market_cap_single(symbol: str, min_cap: int) -> str | None:
    """1銘柄の時価総額チェック。合格すればsymbolを返す"""
    try:
        info = yf.Ticker(symbol).info
        cap = info.get("marketCap") or info.get("market_cap") or 0
        return symbol if cap >= min_cap else None
    except Exception:
        return None


def filter_by_market_cap(symbols: list[str], config: dict) -> list[str]:
    """$2B以上の銘柄に絞り込む（バッチ並列 — Crumb無効化を防ぐため低並列）"""
    min_cap     = config["market_cap"]["min_market_cap"]
    workers     = config["performance"]["market_cap_workers"]
    batch_size  = config["performance"].get("market_cap_batch_size", 30)
    batch_delay = config["performance"].get("market_cap_batch_delay_seconds", 2.0)
    passed      = []
    total       = len(symbols)

    logging.info(f"[MarketCap] Filtering {total} symbols (min ${min_cap/1e9:.1f}B, {workers} workers, batch={batch_size})...")

    batches = [symbols[i:i+batch_size] for i in range(0, len(symbols), batch_size)]
    done    = 0

    for batch_idx, batch in enumerate(batches):
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_fetch_market_cap_single, sym, min_cap): sym for sym in batch}
            for future in as_completed(futures):
                done += 1
                result = future.result()
                if result:
                    passed.append(result)

        if done % 300 == 0 or batch_idx % 10 == 0:
            logging.info(f"[MarketCap] {done}/{total} checked, {len(passed)} passed so far")

        if batch_idx < len(batches) - 1:
            time.sleep(batch_delay)

    logging.info(f"[MarketCap] {len(passed)}/{total} symbols passed filter")
    return passed


# ─── OIスナップショット取得 ───────────────────────────────────────────────

def fetch_oi_snapshot(symbol: str, config: dict) -> dict | None:
    """1銘柄の全限月OIスナップショットを取得"""
    max_days = config["options"]["max_expiry_days"]
    otm_buf  = config["scoring"]["otm_strike_multiplier"]

    try:
        ticker = yf.Ticker(symbol)
        info   = ticker.info
        spot   = (
            info.get("regularMarketPrice")
            or info.get("currentPrice")
            or info.get("previousClose")
        )
        if not spot:
            return None

        expirations = ticker.options
        if not expirations:
            return None

        today     = datetime.now().date()
        cutoff    = today + timedelta(days=max_days)
        valid_exp = [e for e in expirations if datetime.strptime(e, "%Y-%m-%d").date() <= cutoff]
        if not valid_exp:
            return None

        total_call_oi = 0
        total_put_oi  = 0
        otm_call_oi   = 0

        for exp in valid_exp:
            try:
                chain = ticker.option_chain(exp)
                calls = chain.calls
                puts  = chain.puts

                c_oi = int(calls["openInterest"].fillna(0).sum())
                p_oi = int(puts["openInterest"].fillna(0).sum())
                total_call_oi += c_oi
                total_put_oi  += p_oi

                # OTM Call: strike > spot * otm_buf
                otm_mask = calls["strike"] > (spot * otm_buf)
                otm_call_oi += int(calls.loc[otm_mask, "openInterest"].fillna(0).sum())

            except Exception:
                continue

        total_oi = total_call_oi + total_put_oi
        if total_oi == 0:
            return None

        pcr = (total_put_oi / total_call_oi) if total_call_oi > 0 else None
        otm_ratio = (otm_call_oi / total_call_oi) if total_call_oi > 0 else None

        return {
            "symbol":          symbol,
            "spot_price":      round(spot, 4),
            "call_oi":         total_call_oi,
            "put_oi":          total_put_oi,
            "total_oi":        total_oi,
            "pcr":             round(pcr, 4) if pcr is not None else None,
            "otm_call_oi":     otm_call_oi,
            "otm_call_ratio":  round(otm_ratio, 4) if otm_ratio is not None else None,
            "fetch_time":      datetime.utcnow().isoformat(),
        }

    except Exception as e:
        logging.debug(f"[OI] {symbol}: {e}")
        return None


def fetch_oi_snapshots_parallel(symbols: list[str], config: dict) -> dict[str, dict]:
    """全銘柄のOIスナップショットを並列取得"""
    workers     = config["performance"]["oi_workers"]
    batch_size  = config["performance"]["batch_size"]
    batch_delay = config["performance"]["batch_delay_seconds"]
    results: dict[str, dict] = {}

    batches = [symbols[i:i+batch_size] for i in range(0, len(symbols), batch_size)]
    total   = len(symbols)

    logging.info(f"[OI] Fetching {total} symbols in {len(batches)} batches ({workers} workers)...")

    for batch_idx, batch in enumerate(batches):
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch_oi_snapshot, sym, config): sym for sym in batch}
            for future in as_completed(futures):
                sym    = futures[future]
                result = future.result()
                if result:
                    results[sym] = result

        fetched = len(results)
        logging.info(f"[OI] Batch {batch_idx+1}/{len(batches)} done. {fetched} snapshots so far.")

        if batch_idx < len(batches) - 1:
            time.sleep(batch_delay)

    logging.info(f"[OI] Completed: {len(results)}/{total} symbols fetched")
    return results


# ─── R2 OIキャッシュ ─────────────────────────────────────────────────────

def create_r2_client():
    """Cloudflare R2 S3互換クライアントを生成"""
    return boto3.client(
        "s3",
        endpoint_url          = os.environ["R2_ENDPOINT"],
        aws_access_key_id     = os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key = os.environ["R2_SECRET_ACCESS_KEY"],
        region_name           = "auto",
    )


def load_oi_cache_from_r2(date_str: str, s3_client, config: dict) -> dict | None:
    """R2から指定日のOIキャッシュを取得。存在しない/エラーはNoneを返す"""
    prefix = config["r2"]["oi_cache_prefix"]
    key    = f"{prefix}/{date_str}.json"
    bucket = os.environ["R2_BUCKET_NAME"]
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key)
        data = json.loads(resp["Body"].read().decode("utf-8"))
        logging.info(f"[R2] OI cache loaded: {key} ({data.get('symbol_count', '?')} symbols)")
        return data
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NoSuchKey", "404"):
            logging.info(f"[R2] OI cache not found for {date_str} (first run or weekend)")
        else:
            logging.warning(f"[R2] Cache load failed ({code}): {e}")
        return None
    except Exception as e:
        logging.warning(f"[R2] Cache load error: {e}")
        return None


def save_oi_cache_to_r2(date_str: str, snapshots: dict, s3_client, config: dict, dry_run: bool = False) -> bool:
    """当日OIキャッシュをR2に保存"""
    prefix = config["r2"]["oi_cache_prefix"]
    key    = f"{prefix}/{date_str}.json"
    bucket = os.environ["R2_BUCKET_NAME"]

    cache = {
        "date":         date_str,
        "generated_at": datetime.utcnow().isoformat(),
        "symbol_count": len(snapshots),
        "snapshots":    snapshots,
    }
    body = json.dumps(cache, ensure_ascii=False)

    if dry_run:
        logging.info(f"[R2][DRY RUN] Would upload OI cache: {key} ({len(snapshots)} symbols)")
        return True

    try:
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
        logging.info(f"[R2] OI cache saved: {key} ({len(snapshots)} symbols)")
        return True
    except Exception as e:
        logging.warning(f"[R2] Cache save failed: {e}")
        return False


# ─── OI変化率計算・ゲートフィルタ ────────────────────────────────────────

def compute_oi_changes(today_snapshots: dict, yesterday_cache: dict, config: dict) -> dict[str, dict]:
    """
    前日比OI変化を計算し、ゲートフィルタ（変化率+min_oi）を適用する。
    返り値: {symbol: {...変化情報...}} フィルタ通過銘柄のみ
    """
    min_change = config["screening"]["min_oi_change_pct"]
    min_oi     = config["screening"]["min_total_oi"]
    yesterday  = yesterday_cache.get("snapshots", {})
    result     = {}

    for sym, today in today_snapshots.items():
        prev = yesterday.get(sym)
        if prev is None:
            continue

        prev_total_oi = prev.get("total_oi", 0)
        prev_call_oi  = prev.get("call_oi", 0)
        today_total   = today["total_oi"]
        today_call    = today["call_oi"]

        if prev_total_oi <= 0 or today_total < min_oi:
            continue

        total_change_pct = (today_total - prev_total_oi) / prev_total_oi * 100
        if total_change_pct < min_change:
            continue

        call_change_pct = (
            (today_call - prev_call_oi) / prev_call_oi * 100
            if prev_call_oi > 0 else 0.0
        )

        result[sym] = {
            "symbol":              sym,
            "spot_price":          today["spot_price"],
            "total_oi_today":      today_total,
            "total_oi_yesterday":  prev_total_oi,
            "total_oi_change_pct": round(total_change_pct, 2),
            "call_oi_today":       today_call,
            "call_oi_yesterday":   prev_call_oi,
            "call_oi_change_pct":  round(call_change_pct, 2),
            "put_oi_today":        today["put_oi"],
            "pcr":                 today["pcr"],
            "otm_call_ratio":      today["otm_call_ratio"],
        }

    logging.info(f"[Filter] {len(result)} symbols passed OI gate filter")
    return result


# ─── 強気スコア計算 ───────────────────────────────────────────────────────

def score_symbol(change_data: dict, config: dict) -> float:
    """bullish_score (0-100) を計算する純粋関数"""
    sc = config["scoring"]

    # A: Call OI変化率スコア（0-100）
    call_chg = change_data.get("call_oi_change_pct", 0.0)
    a_score  = min(max(call_chg, 0.0) / 100.0 * 100.0, 100.0)

    # B: PCRスコア（低PCR=高スコア）
    pcr = change_data.get("pcr")
    if pcr is None:
        b_score = 0.0
    else:
        pcr_hi  = sc["pcr_threshold_high"]    # 1.5
        pcr_rng = sc["pcr_threshold_range"]   # 1.0
        b_score = max(0.0, min(100.0, (pcr_hi - pcr) / pcr_rng * 100.0))

    # C: OTM Call集中度スコア
    otm_ratio = change_data.get("otm_call_ratio")
    if otm_ratio is None:
        c_score = 0.0
    else:
        otm_cap = sc["otm_call_ratio_cap"]  # 0.7
        c_score = min(otm_ratio / otm_cap * 100.0, 100.0)

    return round(
        a_score * sc["call_oi_change_weight"] +
        b_score * sc["pcr_weight"] +
        c_score * sc["otm_call_ratio_weight"],
        2
    )


def screen_and_rank(change_map: dict, config: dict) -> list[dict]:
    """スコア計算・ソート・top_N抽出"""
    top_n   = config["screening"]["top_n"]
    scored  = []

    for sym, data in change_map.items():
        bs = score_symbol(data, config)
        scored.append({
            "symbol":              sym,
            "bullish_score":       bs,
            "call_oi_change_pct":  data["call_oi_change_pct"],
            "total_oi_change_pct": data["total_oi_change_pct"],
            "total_oi":            data["total_oi_today"],
            "call_oi":             data["call_oi_today"],
            "put_oi":              data["put_oi_today"],
            "pcr":                 data["pcr"],
            "otm_call_ratio":      data["otm_call_ratio"],
            "spot_price":          data["spot_price"],
        })

    scored.sort(key=lambda x: x["bullish_score"], reverse=True)
    return scored[:top_n]


# ─── 出力 ─────────────────────────────────────────────────────────────────

def select_output_symbols(ranked: list[dict], config: dict) -> list[str]:
    """
    always_include銘柄を先頭に置き、ランキング上位銘柄を続ける。
    max_symbolsで上限。
    """
    max_n   = config["output"]["max_symbols"]
    always  = config["output"].get("always_include", [])
    ranked_syms = [r["symbol"] for r in ranked]

    seen    = set()
    result  = []
    for sym in list(always) + ranked_syms:
        if sym not in seen:
            seen.add(sym)
            result.append(sym)
        if len(result) >= max_n:
            break

    return result


def write_output(symbols: list[str], ranked: list[dict], date_str: str) -> None:
    """data/symbols_oi_surge.json に出力"""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date":               date_str,
        "generated_at":       datetime.utcnow().isoformat(),
        "symbols":            symbols,
        "screening_results":  ranked,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"[Output] {OUTPUT_PATH}: {len(symbols)} symbols, {len(ranked)} screening results")


# ─── メイン ───────────────────────────────────────────────────────────────

def _main_impl(args) -> bool:
    config   = load_screener_config()
    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    yesterday_str = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")

    # R2クライアント（dry-run時はR2操作を全てスキップ）
    r2_client = None
    r2_available = False
    if args.dry_run:
        logging.info("[R2] Dry-run mode: skipping all R2 operations")
    else:
        try:
            r2_client    = create_r2_client()
            r2_available = True
        except Exception as e:
            logging.warning(f"[R2] Client creation failed: {e}. Will skip R2 operations.")

    # ─── ユニバース構築 ─────────────────────────────────────────────
    if args.symbols:
        all_symbols = args.symbols
        logging.info(f"[Universe] Override: {all_symbols}")
    else:
        all_symbols = build_universe(config)

    # ─── 時価総額フィルタ ────────────────────────────────────────────
    if args.symbols:
        filtered_symbols = all_symbols  # テスト用オーバーライド時はスキップ
    else:
        filtered_symbols = filter_by_market_cap(all_symbols, config)

    # ─── 前日OIキャッシュをR2から取得 ───────────────────────────────
    yesterday_cache = None
    if r2_available:
        yesterday_cache = load_oi_cache_from_r2(yesterday_str, r2_client, config)

    # ─── 本日OIスナップショット取得 ─────────────────────────────────
    today_snapshots = fetch_oi_snapshots_parallel(filtered_symbols, config)

    # ─── 当日OIキャッシュをR2に保存（翌日の前日比計算用） ────────────
    if r2_available and today_snapshots:
        save_oi_cache_to_r2(date_str, today_snapshots, r2_client, config, dry_run=args.dry_run)

    # ─── 初回実行（前日キャッシュなし）の場合はデフォルト銘柄で終了 ──
    if yesterday_cache is None:
        logging.info("[Screener] No yesterday cache found. First run mode — using always_include symbols.")
        always = config["output"].get("always_include", DEFAULT_SYMBOLS)
        write_output(always, [], date_str)
        return True

    # ─── OIゲートフィルタ・スコアリング ─────────────────────────────
    change_map = compute_oi_changes(today_snapshots, yesterday_cache, config)

    if not change_map:
        logging.info("[Screener] No symbols passed gate filter. Outputting always_include.")
        always = config["output"].get("always_include", DEFAULT_SYMBOLS)
        write_output(always, [], date_str)
        return True

    ranked  = screen_and_rank(change_map, config)
    symbols = select_output_symbols(ranked, config)

    # ─── 出力 ────────────────────────────────────────────────────────
    write_output(symbols, ranked, date_str)

    logging.info("[Screener] Top 5 results:")
    for r in ranked[:5]:
        logging.info(
            f"  {r['symbol']:6s} score={r['bullish_score']:5.1f} "
            f"callChg={r['call_oi_change_pct']:+6.1f}% "
            f"PCR={r['pcr']} OTM={r['otm_call_ratio']}"
        )

    return True


def main(args) -> bool:
    try:
        return _main_impl(args)
    except Exception as e:
        logging.critical(f"[Screener] Fatal error: {e}", exc_info=True)
        # パイプライン継続のためデフォルト銘柄を出力
        try:
            config  = load_screener_config()
            always  = config["output"].get("always_include", DEFAULT_SYMBOLS)
            date_str = args.date or datetime.now().strftime("%Y-%m-%d")
            write_output(always, [], date_str)
        except Exception:
            pass
        return True   # パイプラインは止めない


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OI Surge Screener")
    parser.add_argument(
        "--date", default=None,
        help="対象日付のオーバーライド (YYYY-MM-DD)。デフォルトは本日。"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="R2へのアップロードをスキップ（ローカルテスト用）"
    )
    parser.add_argument(
        "--symbols", nargs="+", default=None,
        help="ユニバースをオーバーライド（テスト用例: --symbols SPY QQQ NVDA）"
    )
    parsed = parser.parse_args()
    sys.exit(0 if main(parsed) else 1)
