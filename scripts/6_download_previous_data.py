"""
6_download_previous_data.py

7_generate_note_article.py の前日比較機能のため、
R2から前日のGEXデータをローカルにダウンロードする。

フロー:
1. 前営業日を特定
2. R2から前日GEXデータを取得
3. data/r2/gex/daily/{prev_date}/ に保存
"""

import os
import sys
import json
import logging
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

from market_calendar import get_previous_market_day, get_pipeline_date

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

GEX_DIR = Path("data/r2/gex/daily")

# コア銘柄は常にダウンロードを保証する（設定変更時のサイレント障害を防ぐ）
CORE_SYMBOLS = ['SPY', 'QQQ', 'SMH', 'IWM', 'NVDA']


def create_r2_client():
    """R2 S3互換クライアントを作成"""
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def get_target_symbols() -> list[str]:
    """
    symbols_oi_surge.json から動的に銘柄リストを取得し、
    CORE_SYMBOLS を必ず含める（設定変更時のサイレント障害を防ぐ）。
    ファイルが存在しない場合はデフォルト銘柄を返す。
    """
    symbols_file = Path("data/symbols_oi_surge.json")
    default_symbols = ["SPY", "QQQ", "SMH", "DIA", "IWM", "NVDA", "AAPL", "TSLA"]

    if not symbols_file.exists():
        logging.warning(f"Symbols file not found: {symbols_file}. Using defaults.")
        # CORE_SYMBOLS を先頭に置き、デフォルトに含まれていないものをマージ
        merged = list(CORE_SYMBOLS)
        for s in default_symbols:
            if s not in merged:
                merged.append(s)
        return merged

    try:
        with open(symbols_file, encoding="utf-8") as f:
            data = json.load(f)
        raw_symbols = data.get("symbols", default_symbols)
        # CORE_SYMBOLS を先頭に確保し、残りを続ける
        merged = list(CORE_SYMBOLS)
        for s in raw_symbols:
            if s not in merged:
                merged.append(s)
        logging.info(f"Target symbols ({len(merged)} total, CORE={CORE_SYMBOLS}): {merged}")
        return merged
    except Exception as e:
        logging.warning(f"Error loading symbols file: {e}. Using defaults with CORE_SYMBOLS.")
        merged = list(CORE_SYMBOLS)
        for s in default_symbols:
            if s not in merged:
                merged.append(s)
        return merged


def download_gex_from_r2(date_str: str, symbols: list[str], s3_client) -> bool:
    """
    R2から指定日のGEXデータを全銘柄分ダウンロードする。
    
    Returns:
        bool: 1つ以上のファイルがダウンロードできたかどうか
    """
    bucket = os.environ["R2_BUCKET_NAME"]
    local_dir = GEX_DIR / date_str
    local_dir.mkdir(parents=True, exist_ok=True)
    
    success_count = 0
    
    for symbol in symbols:
        r2_key = f"gex/daily/{date_str}/{symbol}.json"
        local_path = local_dir / f"{symbol}.json"
        
        try:
            # R2からダウンロード
            response = s3_client.get_object(Bucket=bucket, Key=r2_key)
            data = response["Body"].read()
            
            # ローカルに保存
            with open(local_path, "wb") as f:
                f.write(data)
            
            success_count += 1
            logging.info(f"[{symbol}] Downloaded: {r2_key} -> {local_path}")
            
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code in ("NoSuchKey", "404"):
                logging.warning(f"[{symbol}] Not found in R2: {r2_key}")
            else:
                logging.warning(f"[{symbol}] R2 download failed ({error_code}): {e}")
        except Exception as e:
            logging.error(f"[{symbol}] Unexpected error: {e}")
    
    logging.info(f"Downloaded {success_count}/{len(symbols)} GEX files for {date_str}")
    return success_count > 0


def main():
    today = get_pipeline_date()
    
    # 前営業日を特定
    prev_market_day = get_previous_market_day(today)
    if not prev_market_day:
        logging.error("Could not determine previous market day")
        return False
    
    logging.info(f"Previous market day: {prev_market_day}")
    
    # 前日データが既にローカルに存在するかチェック
    prev_dir = GEX_DIR / prev_market_day
    if prev_dir.exists() and any(prev_dir.glob("*.json")):
        logging.info(f"Previous day data already exists locally: {prev_dir}")
        return True
    
    # R2クライアント作成
    try:
        s3_client = create_r2_client()
    except Exception as e:
        logging.error(f"Failed to create R2 client: {e}")
        return False
    
    # 銘柄リスト取得
    symbols = get_target_symbols()
    
    # R2から前日データをダウンロード
    success = download_gex_from_r2(prev_market_day, symbols, s3_client)
    
    if success:
        logging.info("Previous day data download completed successfully")
        return True
    else:
        logging.warning("No previous day data could be downloaded")
        # 前日データがない場合でも記事生成は続行すべきなので True を返す
        return True


if __name__ == "__main__":
    if main():
        sys.exit(0)
    else:
        sys.exit(1)