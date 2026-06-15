"""
pull_from_r2.py

ローカルで GEX 記事を生成するために、R2 から当日(+前日)の
levels JSON と チャート PNG をダウンロードする。

取得物:
  - levels JSON : gex/daily/{date}/*.json        → data/r2/gex/daily/{date}/
  - levels JSON : gex/daily/{prev}/*.json        → data/r2/gex/daily/{prev}/  （前日比較用）
  - チャート PNG: charts/{date}/*.png            → data/r2/charts/{date}/

日付:
  - 既定は R2 の gex/daily/ 配下で最新の日付を自動選択。
  - --date YYYY-MM-DD で明示指定も可能。

環境変数（.env または環境）:
  R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME
"""

import os
import sys
import re
import logging
import argparse
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

from market_calendar import get_previous_market_day

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

LOCAL_ROOT = Path("data/r2")
LEVELS_PREFIX = "gex/daily"
CHARTS_PREFIX = "charts"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def create_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def get_latest_date(s3_client, bucket: str) -> str | None:
    """gex/daily/ 配下の日付ディレクトリのうち最新（最大）の YYYY-MM-DD を返す。"""
    resp = s3_client.list_objects_v2(
        Bucket=bucket, Prefix=f"{LEVELS_PREFIX}/", Delimiter="/"
    )
    dates = []
    for cp in resp.get("CommonPrefixes", []):
        name = cp["Prefix"].rstrip("/").split("/")[-1]
        if DATE_RE.match(name):
            dates.append(name)
    return max(dates) if dates else None


def download_prefix(s3_client, bucket: str, prefix: str, local_dir: Path,
                    suffix: str) -> int:
    """指定 prefix 配下で suffix に一致するオブジェクトをローカルに保存する。"""
    local_dir.mkdir(parents=True, exist_ok=True)
    paginator = s3_client.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(suffix):
                continue
            fname = key.split("/")[-1]
            local_path = local_dir / fname
            try:
                resp = s3_client.get_object(Bucket=bucket, Key=key)
                local_path.write_bytes(resp["Body"].read())
                count += 1
            except Exception as e:
                logging.warning(f"  download failed {key}: {e}")
    return count


def download_levels(s3_client, bucket: str, date_str: str) -> int:
    """gex/daily/{date}/*.json → data/r2/gex/daily/{date}/"""
    prefix = f"{LEVELS_PREFIX}/{date_str}/"
    local_dir = LOCAL_ROOT / LEVELS_PREFIX / date_str
    n = download_prefix(s3_client, bucket, prefix, local_dir, ".json")
    logging.info(f"[levels] {n} JSON files → {local_dir}")
    return n


def download_charts(s3_client, bucket: str, date_str: str) -> int:
    """charts/{date}/*.png → data/r2/charts/{date}/"""
    prefix = f"{CHARTS_PREFIX}/{date_str}/"
    local_dir = LOCAL_ROOT / CHARTS_PREFIX / date_str
    n = download_prefix(s3_client, bucket, prefix, local_dir, ".png")
    logging.info(f"[charts] {n} PNG files → {local_dir}")
    return n


def main(date_arg: str | None = None) -> bool:
    if not all(os.environ.get(k) for k in
               ["R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"]):
        logging.error("R2 credentials not found in environment/.env")
        return False

    bucket = os.environ["R2_BUCKET_NAME"]
    s3 = create_r2_client()

    logging.info("=" * 60)
    logging.info("PULL FROM R2")
    logging.info("=" * 60)

    # 対象日付の決定
    date_str = date_arg
    if date_str is None:
        date_str = get_latest_date(s3, bucket)
        if date_str is None:
            logging.error("No date directories found under gex/daily/")
            return False
        logging.info(f"Latest date in R2: {date_str}")
    else:
        if not DATE_RE.match(date_str):
            logging.error(f"Invalid --date (expected YYYY-MM-DD): {date_str}")
            return False
        logging.info(f"Target date (specified): {date_str}")

    # 当日: levels + charts
    n_levels = download_levels(s3, bucket, date_str)
    n_charts = download_charts(s3, bucket, date_str)

    if n_levels == 0:
        logging.error(f"No levels JSON found for {date_str}. Aborting.")
        return False

    # 前営業日: levels のみ（前日比較用）
    prev = get_previous_market_day(date_str)
    if prev:
        n_prev = download_levels(s3, bucket, prev)
        if n_prev == 0:
            logging.warning(f"No previous-day levels for {prev} (comparison will be skipped)")
    else:
        logging.warning("Could not determine previous market day")

    logging.info("=" * 60)
    logging.info(f"Done. date={date_str}, levels={n_levels}, charts={n_charts}")
    logging.info("=" * 60)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pull GEX data/charts from R2 for local article generation")
    parser.add_argument("--date", default=None, help="対象日付 YYYY-MM-DD（既定: R2の最新日付）")
    args = parser.parse_args()
    sys.exit(0 if main(args.date) else 1)
