# -*- coding: utf-8 -*-
"""GitHub Actions の daily-gex-update と同じ手順を、手元の PC で動かす。

使い方:
  python scripts/run_local_pipeline.py            # 本番（R2 へアップロード）
  python scripts/run_local_pipeline.py --no-upload # 試運転（アップロードしない）

- 作業はリポジトリの外の一時フォルダ（%LOCALAPPDATA%/gex_local_run）で行い、
  リポジトリの data/ には触れない（記事用の data/r2 を壊さないため）。
- R2 の鍵はリポジトリの .env から読む。
- 米東部の 0 時を過ぎるとオプションのデータが壊れるので、その時刻以降は実行しない。
- ログは %LOCALAPPDATA%/gex_local_run_logs/{ET日付}.log
"""
import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
WORK = os.path.join(BASE, "gex_local_run")
LOGS = os.path.join(BASE, "gex_local_run_logs")


def load_env():
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    with open(os.path.join(REPO, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--force", action="store_true", help="時刻の確認を飛ばす（試運転用）")
    a = ap.parse_args()

    et = datetime.now(ZoneInfo("America/New_York"))
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    from market_calendar import is_market_day
    today = et.strftime("%Y-%m-%d")
    if not a.force:
        if not is_market_day(today):
            _boot(f"skip: {today} (ET) holiday"); return 0
        if et.hour < 16 or et.hour >= 23:
            _boot(f"skip: ET {et:%H:%M} outside 16-23"); return 0

    os.makedirs(LOGS, exist_ok=True)
    log = open(os.path.join(LOGS, f"{today}.log"), "a", encoding="utf-8")
    shutil.rmtree(WORK, ignore_errors=True)
    for d in ["options", "gex", "levels", "iv_history", "charts"]:
        os.makedirs(os.path.join(WORK, "data", d), exist_ok=True)
    shutil.copytree(os.path.join(REPO, "config"), os.path.join(WORK, "config"))
    env = load_env()
    py = sys.executable
    S = lambda n: os.path.join(REPO, "scripts", n)

    steps = [
        ("0. Screen OI surge", [py, S("0_screen_oi_surge.py")], True),
        ("wait", None, True),
        ("1. Fetch options", [py, S("1_fetch_options_data.py"), "--symbols-from", "data/symbols_oi_surge.json"], False),
        ("2. Calculate GEX", [py, S("2_calculate_gex.py")], False),
        ("3. Extract levels", [py, S("3_extract_levels.py")], False),
        ("4. Export JSON", [py, S("4_export_to_json.py")], False),
        ("5. Upload to R2", [py, S("5_upload_to_r2.py")], False),
        ("6. Download previous", [py, S("6_download_previous_data.py")], True),
        ("6. Visualize", [py, S("visualize_gex.py")], False),
        ("6. Upload charts", [py, S("5_upload_to_r2.py"), "--charts-only"], True),
    ]
    rc = 0
    for name, cmd, soft in steps:
        if name == "wait":
            import time; time.sleep(120); continue
        if a.no_upload and "Upload" in name:
            log.write(f"== {name}: skipped (--no-upload)\n"); continue
        log.write(f"== {name} {datetime.now():%H:%M:%S}\n"); log.flush()
        r = subprocess.run(cmd, cwd=WORK, env=env, stdout=log, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            log.write(f"!! {name} failed ({r.returncode})\n")
            if not soft:
                rc = r.returncode; break
    log.write(f"== done rc={rc} {datetime.now():%H:%M:%S}\n"); log.close()
    return rc


def _boot(msg):
    os.makedirs(LOGS, exist_ok=True)
    with open(os.path.join(LOGS, "_launcher.log"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}" + chr(10))


if __name__ == "__main__":
    try:
        _boot(f"start argv={sys.argv[1:]} cwd={os.getcwd()} py={sys.executable}")
        rc = main()
        _boot(f"end rc={rc}")
    except BaseException:
        import traceback
        _boot("EXCEPTION" + chr(10) + traceback.format_exc())
        raise
    sys.exit(rc)
