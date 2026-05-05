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
from datetime import date, datetime, timedelta
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

GEX_DIR = Path("data/r2/gex/daily")


def get_previous_market_day(date_str: str) -> str | None:
    """
    指定日の前営業日を取得する（7_generate_note_article.py と同一ロジック）。
    """
    current = datetime.strptime(date_str, "%Y-%m-%d")
    
    # 米国主要祝日（簡易版）
    holidays_2026 = {
        "2026-01-01",  # New Year's Day
        "2026-01-20",  # MLK Day
        "2026-02-17",  # Presidents Day
        "2026-05-25",  # Memorial Day
        "2026-07-03",  # Independence Day (observed)
        "2026-09-07",  # Labor Day
        "2026-11-26",  # Thanksgiving
        "2026-12-25",  # Christmas
    }
    
    # 1日ずつ遡って営業日を探す
    for i in range(1, 8):  # 最大7日遡る
        prev_date = current - timedelta(days=i)
        prev_str = prev_date.strftime("%Y-%m-%d")
        
        # 土日をスキップ
        if prev_date.weekday() >= 5:  # 5=土, 6=日
            continue
            
        # 祝日をスキップ
        if prev_str in holidays_2026:
            continue
            
        return prev_str
    
    logging.warning(f"Could not find previous market day within 7 days of {date_str}")
    return None


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
    symbols_oi_surge.json から動的に銘柄リストを取得。
    ファイルが存在しない場合はデフォルト銘柄を返す。
    """
    symbols_file = Path("data/symbols_oi_surge.json")
    default_symbols = ["SPY", "QQQ", "SMH", "DIA", "IWM", "NVDA", "AAPL", "TSLA"]
    
    if not symbols_file.exists():
        logging.warning(f"Symbols file not found: {symbols_file}. Using defaults.")
        return default_symbols
    
    try:
        with open(symbols_file, encoding="utf-8") as f:
            data = json.load(f)
        symbols = data.get("symbols", default_symbols)
        logging.info(f"Target symbols: {symbols}")
        return symbols
    except Exception as e:
        logging.warning(f"Error loading symbols file: {e}. Using defaults.")
        return default_symbols


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
    today = date.today().isoformat()
    
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