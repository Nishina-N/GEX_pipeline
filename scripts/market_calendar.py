"""
market_calendar.py

NYSE営業日判定の共通ロジック。
パイプライン全体で日付を一元管理するユーティリティを提供する。

【日付管理の設計】
各スクリプトが独立に datetime.now() / date.today() を呼ぶと、
パイプラインが深夜0時UTC をまたいだ際に日付がずれる問題がある。
また、土日・祝日に手動実行した際も誤った日付でデータが生成される。

解決策:
  Step 1 (1_fetch_options_data.py) の開始時に get_effective_market_date() で
  「有効な市場営業日」を決定し、data/effective_date.txt に書き込む。
  以降のスクリプトはすべて get_pipeline_date() でこのファイルを読んで日付を取得する。
"""

import os
import logging
from datetime import date, datetime, timedelta


EFFECTIVE_DATE_FILE = "data/effective_date.txt"


def _observed_holiday(year: int, month: int, day: int) -> date:
    d = date(year, month, day)
    if d.weekday() == 5:
        d -= timedelta(days=1)
    elif d.weekday() == 6:
        d += timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    if n > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + (n - 1) * 7)
    last = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def _easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def nyse_holidays(year: int) -> set[str]:
    """指定年のNYSE休場日セットを返す（ISO形式文字列）。"""
    h = {
        _observed_holiday(year, 1, 1).isoformat(),   # New Year's Day
        _nth_weekday(year, 1, 0, 3).isoformat(),      # MLK Day
        _nth_weekday(year, 2, 0, 3).isoformat(),      # Presidents' Day
        (_easter(year) - timedelta(days=2)).isoformat(),  # Good Friday
        _nth_weekday(year, 5, 0, -1).isoformat(),     # Memorial Day
        _observed_holiday(year, 7, 4).isoformat(),    # Independence Day
        _nth_weekday(year, 9, 0, 1).isoformat(),      # Labor Day
        _nth_weekday(year, 11, 3, 4).isoformat(),     # Thanksgiving
        _observed_holiday(year, 12, 25).isoformat(),  # Christmas
    }
    if year >= 2022:
        h.add(_observed_holiday(year, 6, 19).isoformat())  # Juneteenth
    return h


def get_previous_market_day(date_str: str) -> str | None:
    """指定日の前営業日を返す（NYSE休場日・土日を除く）。"""
    current = datetime.strptime(date_str, "%Y-%m-%d")
    for i in range(1, 10):
        prev = current - timedelta(days=i)
        if prev.weekday() >= 5:
            continue
        prev_str = prev.strftime("%Y-%m-%d")
        if prev_str in nyse_holidays(prev.year):
            continue
        return prev_str
    logging.warning(f"Could not find previous market day within 10 days of {date_str}")
    return None


def is_market_day(date_str: str) -> bool:
    """指定日がNYSE営業日かどうかを判定する（土日・祝日は False）。"""
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    if d.weekday() >= 5:   # 土日
        return False
    return date_str not in nyse_holidays(d.year)


def get_effective_market_date(base_date: str | None = None) -> str:
    """
    本日（または base_date）が NYSE 営業日なら返す。
    土日・祝日の場合は直前の営業日を返す。

    例:
      月曜: そのまま月曜を返す
      土曜: 前の金曜を返す
      月曜（祝日）: 前の金曜を返す
    """
    if base_date is None:
        base_date = date.today().isoformat()
    if is_market_day(base_date):
        return base_date
    prev = get_previous_market_day(base_date)
    if prev is None:
        logging.error(f"Could not find effective market date from {base_date}")
        return base_date
    logging.info(f"{base_date} is not a market day. Using previous market day: {prev}")
    return prev


def get_pipeline_date() -> str:
    """
    data/effective_date.txt から日付を読んで返す。
    ファイルが存在しない場合は get_effective_market_date() で算出して返す。

    Step 2〜7 のスクリプトはこの関数で日付を取得すること。
    """
    try:
        with open(EFFECTIVE_DATE_FILE, "r") as f:
            d = f.read().strip()
            if d:
                logging.debug(f"Pipeline date loaded from file: {d}")
                return d
    except FileNotFoundError:
        pass
    fallback = get_effective_market_date()
    logging.warning(
        f"{EFFECTIVE_DATE_FILE} not found. "
        f"Falling back to get_effective_market_date(): {fallback}"
    )
    return fallback


def set_pipeline_date(date_str: str) -> None:
    """
    data/effective_date.txt に日付を書き込む。
    Step 1 (1_fetch_options_data.py) の開始時に必ず呼ぶこと。
    """
    dir_path = os.path.dirname(EFFECTIVE_DATE_FILE)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    with open(EFFECTIVE_DATE_FILE, "w") as f:
        f.write(date_str)
    logging.info(f"Pipeline date set to: {date_str} → written to {EFFECTIVE_DATE_FILE}")
