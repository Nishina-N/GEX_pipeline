"""R2 の IVサマリを ローカルに恒久アーカイブする.

R2 側は `cleanup_old_iv_history()` が 30日超の iv_history/ を pkl.gz と
_iv_summary.json の両方削除する（5_upload_to_r2.py:560 が `_iv_summary` も
日付として解釈するため）。つまり **R2 の IV履歴は常に直近30日しか残らない**。

σ_20MA（タスク#8）は N=20 なので 30日あれば足りるが、長期パーセンタイルや
再検証には足りない。そこで本スクリプトで日次に取りこぼしなく吸い出して
`data/r2/iv_history/{symbol}/{date}.json` に貯める。

    python scripts/pull_iv_history.py          # 未取得分だけ差分ダウンロード
    python scripts/pull_iv_history.py --force  # 既存も上書き
"""
import argparse
import json
import logging
import os
from pathlib import Path

import boto3
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "r2" / "iv_history"
PREFIX = "iv_history/"


def make_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )


def main():
    ap = argparse.ArgumentParser(description="Archive R2 IV summaries locally")
    ap.add_argument("--force", action="store_true", help="既存ファイルも上書きする")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    s3 = make_client()
    bucket = os.environ["R2_BUCKET_NAME"]

    fetched = skipped = failed = 0
    symbols = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith("_iv_summary.json"):
                continue  # pkl.gz は重いので取らない。サマリだけ貯める
            parts = key.split("/")
            if len(parts) < 3:
                continue
            symbol = parts[1]
            date_str = os.path.basename(key).replace("_iv_summary.json", "")

            dest = OUT_DIR / symbol / f"{date_str}.json"
            if dest.exists() and not args.force:
                skipped += 1
                continue
            try:
                body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                json.loads(body)  # 壊れたJSONを貯めない
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(body)
                fetched += 1
                symbols.add(symbol)
            except Exception as e:
                logging.warning(f"  failed {key}: {e}")
                failed += 1

    total = sum(1 for _ in OUT_DIR.rglob("*.json")) if OUT_DIR.exists() else 0
    logging.info(
        f"Done. fetched={fetched} skipped={skipped} failed={failed} "
        f"/ archive total={total} files, {len(list(OUT_DIR.iterdir())) if OUT_DIR.exists() else 0} symbols"
    )


if __name__ == "__main__":
    main()
