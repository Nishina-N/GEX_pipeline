# -*- coding: utf-8 -*-
"""パイプラインを今動かしてよいかを判定する（CI と手元の実行で共通）。

止める条件:
  1. 米東部の日付が休場日
  2. 米東部で 16:00 より前（0 時を過ぎて引け前）。この時間帯は yfinance のオプションの
     データが壊れる（2026-11-24 に実例: 取得件数が激減、HVL が異常値）。
  3. R2 にその日のデータ（gex/daily/{date}/SPY.json）がもうある（手元の実行などで済んでいる）

結果は標準出力に run=true/false を出し、GITHUB_OUTPUT があればそこにも書く。
終了コードは常に 0（判定だけ）。
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market_calendar import is_market_day

load_dotenv()


def decide():
    et = datetime.now(ZoneInfo("America/New_York"))
    date = et.strftime("%Y-%m-%d")
    if not is_market_day(date):
        return False, f"{date} (ET) は休場日"
    if et.hour < 16:
        return False, f"ET {et:%H:%M}: 引け前（0時過ぎ）はオプションのデータが壊れるので実行しない"
    try:
        s3 = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
                          aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                          aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto")
        s3.head_object(Bucket=os.environ["R2_BUCKET_NAME"], Key=f"gex/daily/{date}/SPY.json")
        return False, f"R2 に {date} のデータがもうある"
    except KeyError as e:
        return True, f"R2 の設定がない（{e}）ので確認せずに実行"
    except Exception:
        return True, f"{date} のデータはまだない"


if __name__ == "__main__":
    run, why = decide()
    print(f"run={'true' if run else 'false'}  ({why})")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"run={'true' if run else 'false'}\n")
