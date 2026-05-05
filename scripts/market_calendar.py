"""
market_calendar.py

NYSE営業日判定の共通ロジック。
6_download_previous_data.py と 7_generate_note_article.py で共用する。
"""

import logging
from datetime import date, datetime, timedelta


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
