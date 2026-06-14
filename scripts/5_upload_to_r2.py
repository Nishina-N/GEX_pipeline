"""
5_upload_to_r2.py

GEXデータを Cloudflare R2 にアップロードする。

アップロード内容:
  [既存]
  - gex/daily/{date}/{symbol}.json      : GEXレベル（levels JSON）
  - gex/daily/latest.json               : メタデータ
  [新規]
  - options/daily/{date}/{symbol}.pkl.gz     : 生オプションチェーン（gzip圧縮）
  - gex/daily/{date}/{symbol}_gex.pkl.gz     : GEX計算結果（gzip圧縮）
  - iv_history/{symbol}/{date}.pkl.gz        : IVスナップショット（gzip圧縮）
  - iv_history/{symbol}/{date}_iv_summary.json : IVサマリ（AI可読、日付別蓄積）
  - gex/history/{symbol}_metrics.json        : キーメトリクス時系列（AI可読、累積追記）
"""
import os
import sys
import json
import gzip
import pickle
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import numpy as np
import pandas as pd

load_dotenv()

R2_ENDPOINT         = os.getenv('R2_ENDPOINT')
R2_ACCESS_KEY_ID    = os.getenv('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.getenv('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME      = os.getenv('R2_BUCKET_NAME')

DATA_FOLDER  = "data"
R2_OUTPUT    = os.path.join(DATA_FOLDER, "r2")
OPTIONS_DIR  = os.path.join(DATA_FOLDER, "options")
GEX_DIR      = os.path.join(DATA_FOLDER, "gex")
LEVELS_DIR   = os.path.join(DATA_FOLDER, "levels")
IV_HIST_DIR  = os.path.join(DATA_FOLDER, "iv_history")
CHARTS_DIR   = os.path.join(DATA_FOLDER, "charts")

MAX_WORKERS = 5

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "settings.json")

from market_calendar import get_pipeline_date

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def create_s3_client():
    return boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name='auto'
    )


# ─────────────────────────────────────────────────────────────
# 共通アップロードヘルパー
# ─────────────────────────────────────────────────────────────

def upload_single_file(endpoint, access_key, secret_key, bucket_name,
                       file_path, key, max_retries=3):
    """JSONファイルをR2にアップロード（リトライ付き）"""
    s3_client = None
    try:
        s3_client = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name='auto'
        )
        for attempt in range(max_retries):
            try:
                s3_client.upload_file(
                    file_path, bucket_name, key,
                    ExtraArgs={'ContentType': 'application/json'}
                )
                return True
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                logging.warning(f"Retry {attempt + 1}/{max_retries} for {key}: {e}")
        return False
    finally:
        if s3_client:
            s3_client.close()


def compress_and_upload_pkl(s3_client, local_path, s3_key, max_retries=3):
    """
    pkl ファイルを gzip 圧縮してメモリ経由で R2 にアップロードする。
    ファイルサイズをログに出力する。
    """
    with open(local_path, 'rb') as f:
        raw = f.read()
    compressed = gzip.compress(raw, compresslevel=6)

    ratio = len(compressed) / len(raw) * 100
    logging.info(
        f"  {os.path.basename(local_path)}: "
        f"{len(raw)/1024:.0f}KB → {len(compressed)/1024:.0f}KB ({ratio:.0f}%)"
    )

    for attempt in range(max_retries):
        try:
            s3_client.put_object(
                Bucket=R2_BUCKET_NAME,
                Key=s3_key,
                Body=compressed,
                ContentType='application/octet-stream',
                ContentEncoding='gzip'
            )
            return True
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            logging.warning(f"Retry {attempt + 1}/{max_retries} for {s3_key}: {e}")
    return False


def upload_json_to_r2(s3_client, data, s3_key, max_retries=3):
    """dict を JSON 化してR2にアップロードする"""
    body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
    for attempt in range(max_retries):
        try:
            s3_client.put_object(
                Bucket=R2_BUCKET_NAME,
                Key=s3_key,
                Body=body,
                ContentType='application/json; charset=utf-8'
            )
            return True
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            logging.warning(f"Retry {attempt + 1}/{max_retries} for {s3_key}: {e}")
    return False


# ─────────────────────────────────────────────────────────────
# 既存: levels JSON のアップロード
# ─────────────────────────────────────────────────────────────

def upload_levels_json(date_str):
    """既存の levels JSON を R2 の gex/daily/{date}/ にアップロードする"""
    gex_r2_dir = os.path.join(R2_OUTPUT, "gex")
    if not os.path.exists(gex_r2_dir):
        logging.warning(f"GEX R2 directory not found: {gex_r2_dir}")
        return 0, 0

    all_files = []
    for root, dirs, files in os.walk(gex_r2_dir):
        for file in files:
            if not file.endswith('.json'):
                continue
            local_path = os.path.join(root, file)
            relative_path = os.path.relpath(local_path, R2_OUTPUT)
            s3_key = relative_path.replace('\\', '/')
            all_files.append((local_path, s3_key))

    success, fail = 0, 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(
                upload_single_file,
                R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
                R2_BUCKET_NAME, local_path, s3_key
            ): s3_key
            for local_path, s3_key in all_files
        }
        for future in as_completed(futures):
            s3_key = futures[future]
            try:
                if future.result():
                    success += 1
                    logging.info(f"  ✅ {s3_key}")
                else:
                    fail += 1
            except Exception as e:
                fail += 1
                logging.error(f"  ❌ {s3_key}: {e}")
    return success, fail


# ─────────────────────────────────────────────────────────────
# 新規: 生オプションチェーン（圧縮）
# ─────────────────────────────────────────────────────────────

def upload_options_pkls(s3_client, date_str):
    """data/options/{symbol}.pkl を圧縮して R2 の options/daily/{date}/ にアップロード"""
    if not os.path.exists(OPTIONS_DIR):
        logging.warning(f"Options dir not found: {OPTIONS_DIR}")
        return 0, 0

    success, fail = 0, 0
    for fname in os.listdir(OPTIONS_DIR):
        if not fname.endswith('.pkl'):
            continue
        symbol = fname.replace('.pkl', '')
        local_path = os.path.join(OPTIONS_DIR, fname)
        s3_key = f"options/daily/{date_str}/{symbol}.pkl.gz"
        try:
            compress_and_upload_pkl(s3_client, local_path, s3_key)
            logging.info(f"  ✅ {s3_key}")
            success += 1
        except Exception as e:
            logging.error(f"  ❌ {s3_key}: {e}")
            fail += 1
    return success, fail


# ─────────────────────────────────────────────────────────────
# 新規: GEX計算結果（圧縮）
# ─────────────────────────────────────────────────────────────

def upload_gex_pkls(s3_client, date_str):
    """data/gex/{symbol}.pkl を圧縮して R2 の gex/daily/{date}/ にアップロード"""
    if not os.path.exists(GEX_DIR):
        logging.warning(f"GEX dir not found: {GEX_DIR}")
        return 0, 0

    success, fail = 0, 0
    for fname in os.listdir(GEX_DIR):
        if not fname.endswith('.pkl'):
            continue
        symbol = fname.replace('.pkl', '')
        local_path = os.path.join(GEX_DIR, fname)
        s3_key = f"gex/daily/{date_str}/{symbol}_gex.pkl.gz"
        try:
            compress_and_upload_pkl(s3_client, local_path, s3_key)
            logging.info(f"  ✅ {s3_key}")
            success += 1
        except Exception as e:
            logging.error(f"  ❌ {s3_key}: {e}")
            fail += 1
    return success, fail


# ─────────────────────────────────────────────────────────────
# 新規: IVスナップショット（圧縮）+ IVサマリJSON
# ─────────────────────────────────────────────────────────────

def _build_iv_summary(symbol, date_str, iv_df, spot_price):
    """
    IV DataFrame から満期別のサマリ（ATM IV・25Δスキュー等）を生成する。

    25Δ近似: ATMストライクから ±σ√T 程度のストライクを参照。
    簡略化として上位/下位 25% 分位のストライクを使用。
    """
    summary = {
        'date': date_str,
        'symbol': symbol,
        'spotPrice': spot_price,
        'expirations': []
    }

    if iv_df is None or iv_df.empty:
        return summary

    today = pd.Timestamp(date_str)

    for exp, grp in iv_df.groupby('expiration'):
        exp_date = pd.Timestamp(exp)
        dte = max(0, (exp_date - today).days)

        # ATM: spot に最も近いストライク
        grp = grp.copy()
        grp['dist'] = (grp['strike'] - spot_price).abs()
        atm_row = grp.loc[grp['dist'].idxmin()]
        atm_iv = float(atm_row['impliedVolatility']) if pd.notna(atm_row['impliedVolatility']) else None

        # 25Δ put/call 近似: put は ATM より低い上位25%ile、call は高い上位25%ile
        puts  = grp[grp['optionType'] == 'put'].copy()
        calls = grp[grp['optionType'] == 'call'].copy()

        put_25d_iv, call_25d_iv, skew = None, None, None
        if not puts.empty and not calls.empty:
            put_low_strikes = puts[puts['strike'] <= spot_price]
            if not put_low_strikes.empty:
                q25_put = put_low_strikes['strike'].quantile(0.75)  # 下から75% = ATMに近い25Δ側
                row = put_low_strikes.iloc[(put_low_strikes['strike'] - q25_put).abs().argsort()[:1]]
                put_25d_iv = float(row['impliedVolatility'].values[0]) if pd.notna(row['impliedVolatility'].values[0]) else None

            call_high_strikes = calls[calls['strike'] >= spot_price]
            if not call_high_strikes.empty:
                q75_call = call_high_strikes['strike'].quantile(0.25)
                row = call_high_strikes.iloc[(call_high_strikes['strike'] - q75_call).abs().argsort()[:1]]
                call_25d_iv = float(row['impliedVolatility'].values[0]) if pd.notna(row['impliedVolatility'].values[0]) else None

            if put_25d_iv is not None and call_25d_iv is not None:
                skew = round(put_25d_iv - call_25d_iv, 4)

        summary['expirations'].append({
            'expiration': exp,
            'dte': dte,
            'atm_iv': round(atm_iv, 4) if atm_iv is not None else None,
            'put_25d_iv': round(put_25d_iv, 4) if put_25d_iv is not None else None,
            'call_25d_iv': round(call_25d_iv, 4) if call_25d_iv is not None else None,
            'skew': skew,
        })

    # DTE 昇順でソート
    summary['expirations'].sort(key=lambda x: x['dte'])
    return summary


def upload_iv_history_pkls(s3_client, date_str):
    """
    data/iv_history/{symbol}/{date}.pkl を:
    1. gzip 圧縮して R2 の iv_history/{symbol}/{date}.pkl.gz にアップロード
    2. IVサマリ JSON を生成して iv_history/{symbol}/{date}_iv_summary.json にアップロード
    """
    if not os.path.exists(IV_HIST_DIR):
        logging.warning(f"IV history dir not found: {IV_HIST_DIR}")
        return 0, 0

    success, fail = 0, 0

    for symbol in os.listdir(IV_HIST_DIR):
        sym_dir = os.path.join(IV_HIST_DIR, symbol)
        if not os.path.isdir(sym_dir):
            continue

        pkl_path = os.path.join(sym_dir, f"{date_str}.pkl")
        if not os.path.exists(pkl_path):
            logging.debug(f"  [{symbol}] IV snapshot not found for {date_str}, skipping")
            continue

        # 1. 圧縮 pkl アップロード
        s3_key_pkl = f"iv_history/{symbol}/{date_str}.pkl.gz"
        try:
            compress_and_upload_pkl(s3_client, pkl_path, s3_key_pkl)
            logging.info(f"  ✅ {s3_key_pkl}")
            success += 1
        except Exception as e:
            logging.error(f"  ❌ {s3_key_pkl}: {e}")
            fail += 1
            continue

        # 2. IVサマリ JSON 生成 & アップロード
        s3_key_json = f"iv_history/{symbol}/{date_str}_iv_summary.json"
        try:
            with open(pkl_path, 'rb') as f:
                iv_df = pickle.load(f)

            # spot_price を options pkl から取得（存在する場合）
            spot_price = None
            opt_path = os.path.join(OPTIONS_DIR, f"{symbol}.pkl")
            if os.path.exists(opt_path):
                with open(opt_path, 'rb') as f:
                    opt_data = pickle.load(f)
                spot_price = opt_data.get('spot_price')

            iv_summary = _build_iv_summary(symbol, date_str, iv_df, spot_price)
            upload_json_to_r2(s3_client, iv_summary, s3_key_json)
            logging.info(f"  ✅ {s3_key_json}")
            success += 1
        except Exception as e:
            logging.error(f"  ❌ {s3_key_json}: {e}")
            fail += 1

    return success, fail


# ─────────────────────────────────────────────────────────────
# 新規: GEXキーメトリクス時系列（累積追記）
# ─────────────────────────────────────────────────────────────

def upload_gex_metrics(s3_client, date_str):
    """
    levels JSON から当日のキーメトリクスを抽出し、
    R2 の gex/history/{symbol}_metrics.json に追記アップロードする。

    既存の metrics.json をダウンロードして当日分を追記・アップロード。
    """
    if not os.path.exists(LEVELS_DIR):
        return 0, 0

    success, fail = 0, 0

    for fname in os.listdir(LEVELS_DIR):
        if not fname.endswith('.json'):
            continue
        symbol = fname.replace('.json', '')
        local_path = os.path.join(LEVELS_DIR, fname)
        s3_key = f"gex/history/{symbol}_metrics.json"

        try:
            with open(local_path, 'r') as f:
                data = json.load(f)

            today_entry = {
                'date': date_str,
                'spotPrice': data.get('spotPrice'),
                'totalGEX': data.get('totalGEX'),
                'sentiment': data.get('sentiment'),
                'hvl': data.get('levels', {}).get('hvl'),
                'callWall': data.get('levels', {}).get('callWall'),
                'putWall': data.get('levels', {}).get('putWall'),
            }

            # 既存の metrics.json をダウンロードして追記
            history = []
            try:
                resp = s3_client.get_object(Bucket=R2_BUCKET_NAME, Key=s3_key)
                history = json.loads(resp['Body'].read().decode('utf-8'))
            except s3_client.exceptions.NoSuchKey:
                pass  # 初回は空リストから開始
            except Exception:
                pass  # ダウンロード失敗時も空リストで続行

            # 同日エントリを上書き（重複防止）
            history = [e for e in history if e.get('date') != date_str]
            history.append(today_entry)
            history.sort(key=lambda x: x['date'])

            upload_json_to_r2(s3_client, history, s3_key)
            logging.info(f"  ✅ {s3_key} ({len(history)} entries)")
            success += 1
        except Exception as e:
            logging.error(f"  ❌ {s3_key}: {e}")
            fail += 1

    return success, fail


# ─────────────────────────────────────────────────────────────
# 新規: チャート PNG（visualize 後に実行）
# ─────────────────────────────────────────────────────────────

def upload_charts(s3_client, date_str):
    """
    data/charts/{symbol}_gex.png を R2 の
    charts/{date}/{date}_{symbol}_gex.png にアップロードする。

    Obsidian の attachments に置いても衝突しないよう、
    ファイル名に日付プレフィックスを付与する。
    """
    if not os.path.exists(CHARTS_DIR):
        logging.warning(f"Charts dir not found: {CHARTS_DIR}")
        return 0, 0

    success, fail = 0, 0
    for fname in os.listdir(CHARTS_DIR):
        if not fname.endswith('.png'):
            continue
        local_path = os.path.join(CHARTS_DIR, fname)
        s3_key = f"charts/{date_str}/{date_str}_{fname}"
        for attempt in range(3):
            try:
                s3_client.upload_file(
                    local_path, R2_BUCKET_NAME, s3_key,
                    ExtraArgs={'ContentType': 'image/png'}
                )
                logging.info(f"  ✅ {s3_key}")
                success += 1
                break
            except Exception as e:
                if attempt == 2:
                    logging.error(f"  ❌ {s3_key}: {e}")
                    fail += 1
                else:
                    logging.warning(f"Retry {attempt + 1}/3 for {s3_key}: {e}")
    return success, fail


def upload_charts_only():
    """
    チャート PNG のみを R2 にアップロードする（visualize 後の専用ステップ用）。
    チャートは step5(main) より後に描画されるため、別エントリで実行する。
    """
    if not all([R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME]):
        logging.error("R2 credentials not found in .env")
        return False

    date_str = get_pipeline_date()
    s3 = create_s3_client()

    logging.info("=" * 60)
    logging.info("UPLOAD CHARTS TO R2")
    logging.info("=" * 60)

    s, f = upload_charts(s3, date_str)
    s3.close()

    logging.info("=" * 60)
    logging.info(f"Charts: ✅ {s} uploaded, ❌ {f} failed")
    logging.info("=" * 60)
    return f == 0


# ─────────────────────────────────────────────────────────────
# クリーンアップ
# ─────────────────────────────────────────────────────────────

def cleanup_old_dates(retention_days=30):
    """gex/daily/ 配下の古い日付データを R2 から削除する"""
    try:
        s3_client = create_s3_client()
        prefix = "gex/daily/"
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix=prefix, Delimiter='/')
        cutoff = datetime.now()
        deleted_count = 0

        for page in pages:
            for cp in page.get('CommonPrefixes', []):
                date_prefix = cp['Prefix']
                date_str = date_prefix.replace(prefix, '').rstrip('/')
                if date_str == 'latest.json':
                    continue
                try:
                    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                    if (cutoff - date_obj).days > retention_days:
                        obj_pages = s3_client.get_paginator('list_objects_v2')
                        for op in obj_pages.paginate(Bucket=R2_BUCKET_NAME, Prefix=date_prefix):
                            for obj in op.get('Contents', []):
                                s3_client.delete_object(Bucket=R2_BUCKET_NAME, Key=obj['Key'])
                                deleted_count += 1
                except ValueError:
                    continue

        if deleted_count > 0:
            logging.info(f"Cleaned up {deleted_count} old GEX files from gex/daily/")
        s3_client.close()
    except Exception as e:
        logging.warning(f"GEX cleanup failed (non-critical): {e}")


def cleanup_old_iv_history(s3_client, retention_days=30):
    """iv_history/ 配下の30日超の pkl.gz を R2 から削除する"""
    try:
        prefix = "iv_history/"
        paginator = s3_client.get_paginator('list_objects_v2')
        cutoff_date = (datetime.now() - timedelta(days=retention_days)).strftime('%Y-%m-%d')
        deleted_count = 0

        for page in paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                # キーから日付を抽出: iv_history/{symbol}/{date}.pkl.gz
                basename = os.path.basename(key)
                date_part = basename.split('.')[0].replace('_iv_summary', '')
                try:
                    datetime.strptime(date_part, '%Y-%m-%d')
                    if date_part < cutoff_date:
                        s3_client.delete_object(Bucket=R2_BUCKET_NAME, Key=key)
                        deleted_count += 1
                except ValueError:
                    continue

        if deleted_count > 0:
            logging.info(f"Cleaned up {deleted_count} old IV history files")
    except Exception as e:
        logging.warning(f"IV history cleanup failed (non-critical): {e}")


def cleanup_old_options(s3_client, retention_days=30):
    """options/daily/ 配下の古い pkl.gz を R2 から削除する（オプション、現状は無期限）"""
    # options は無期限蓄積方針のため現状は何もしない
    pass


# ─────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────

def main():
    logging.info("=" * 60)
    logging.info("UPLOAD TO R2")
    logging.info("=" * 60)

    if not all([R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME]):
        logging.error("R2 credentials not found in .env")
        return False

    config = load_config()
    retention_days = config.get('gex_retention_days', 30)
    date_str = get_pipeline_date()

    s3 = create_s3_client()
    total_success, total_fail = 0, 0

    # 1. levels JSON（既存）
    logging.info("\n[1/5] Uploading levels JSON...")
    s, f = upload_levels_json(date_str)
    total_success += s; total_fail += f

    # 2. 生オプションチェーン（圧縮）
    logging.info("\n[2/5] Uploading options pkl (compressed)...")
    s, f = upload_options_pkls(s3, date_str)
    total_success += s; total_fail += f

    # 3. GEX計算結果（圧縮）
    logging.info("\n[3/5] Uploading GEX pkl (compressed)...")
    s, f = upload_gex_pkls(s3, date_str)
    total_success += s; total_fail += f

    # 4. IVスナップショット（圧縮）+ IVサマリJSON
    logging.info("\n[4/5] Uploading IV history (compressed + summary JSON)...")
    s, f = upload_iv_history_pkls(s3, date_str)
    total_success += s; total_fail += f

    # 5. GEXキーメトリクス時系列（累積追記）
    logging.info("\n[5/5] Updating GEX metrics history...")
    s, f = upload_gex_metrics(s3, date_str)
    total_success += s; total_fail += f

    # クリーンアップ
    logging.info("\nCleaning up old data...")
    cleanup_old_dates(retention_days)
    cleanup_old_iv_history(s3, retention_days)

    s3.close()

    logging.info("=" * 60)
    logging.info(f"Total: ✅ {total_success} uploaded, ❌ {total_fail} failed")
    logging.info("=" * 60)

    return total_fail == 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Upload GEX data to R2")
    parser.add_argument(
        '--charts-only', action='store_true',
        help='チャートPNGのみをR2にアップロード（visualize後の専用ステップ用）'
    )
    args = parser.parse_args()

    ok = upload_charts_only() if args.charts_only else main()
    sys.exit(0 if ok else 1)
