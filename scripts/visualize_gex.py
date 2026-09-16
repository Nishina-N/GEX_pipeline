"""
visualize_gex.py

GEX レベルを可視化する。
左パネル: ローソク足 + 出来高 + GEXレベル水平線
右パネル: 短期 / 長期 GEX ヒストグラム（横棒）

Y 軸（価格/ストライク）を左右で共有し、ローソク足の値動きと
GEX の壁の位置を直接比較できるレイアウト。
"""

import os
import sys
import json
import logging

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.ticker as mticker
from matplotlib.patches import Rectangle, ConnectionPatch
import yfinance as yf

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DATA_FOLDER = "data"
LEVELS_DIR = os.path.join(DATA_FOLDER, "levels")
PREV_GEX_DIR = os.path.join(DATA_FOLDER, "r2", "gex", "daily")  # 前営業日の levels
OUTPUT_DIR = os.path.join(DATA_FOLDER, "charts")

# ─── カラーパレット（仕様 §1 準拠） ──────────────────────────
CREAM    = '#F5F5F0'   # 背景（オフホワイト）
INK      = '#2C3E50'   # 墨色（通常バー / ライン）
AMBER    = '#FFBF00'   # 琥珀色（スポット価格）
CRIMSON  = '#E74C3C'   # 朱色（異常 IV / Put Wall）
GREEN    = '#27AE60'   # Call Wall
GRAY     = '#95A5A6'   # 補助色


# ─────────────────────────────────────────────────────────────
# データロード
# ─────────────────────────────────────────────────────────────

def load_gex_levels(symbol):
    path = os.path.join(LEVELS_DIR, f"{symbol}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_prev_profiles(symbol, date_str):
    """前営業日の profile を {panel: {strike: netGEX}} で返す（無ければ None）。

    前日データは step6 (6_download_previous_data.py) が
    data/r2/gex/daily/{prev_date}/{SYMBOL}.json に置く。ローカルでは
    pull_from_r2.py が同じ場所へ落とす。

    参照するのは _prev_session_dir() が返す「直前セッション」1 日分のみ。
    その日に銘柄が無ければ None を返す（＝色分けしない）。
    """
    prev_date = _prev_session_dir(date_str)
    if not prev_date:
        return None

    # 直前セッションのディレクトリ内だけを見る。銘柄が無ければ色分けしない
    # （初登場の OI 急増銘柄を、数週間前の断面と比べてしまうのを防ぐ）。
    path = os.path.join(PREV_GEX_DIR, prev_date, f"{symbol}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            prev = json.load(f)
    except (OSError, ValueError):
        return None

    profile = prev.get('profile') or {}
    out = {}
    for key in ('total', 'short_term', 'long_term'):
        rows = profile.get(key) or []
        out[key] = {float(p['strike']): float(p['netGEX'])
                    for p in rows if p.get('strike') is not None}
    if not any(out.values()):
        return None
    out['_date'] = prev_date
    return out


def _prev_session_dir(date_str):
    """date_str の直前セッションの日付ディレクトリ名を返す。

    休場日は空ディレクトリだけが残ることがある（例: レイバーデー）ので、
    JSON が 1 つ以上入っている最新のディレクトリを「直前セッション」とみなす。
    """
    if not date_str or not os.path.isdir(PREV_GEX_DIR):
        return None
    try:
        cands = sorted(d for d in os.listdir(PREV_GEX_DIR) if d < date_str)
    except OSError:
        return None
    for d in reversed(cands):
        p = os.path.join(PREV_GEX_DIR, d)
        try:
            if any(f.endswith('.json') for f in os.listdir(p)):
                return d
        except OSError:
            continue
    return None


# ─────────────────────────────────────────────────────────────
# 描画ユーティリティ
# ─────────────────────────────────────────────────────────────

def draw_candlesticks(ax, df):
    """ローソク足を Rectangle + ウィックで描画する"""
    for i, (_, row) in enumerate(df.iterrows()):
        if pd.isna(row['Open']) or pd.isna(row['Close']):
            continue
        o = float(row['Open'])
        h = float(row['High'])
        l = float(row['Low'])
        c = float(row['Close'])
        body_bot = min(o, c)
        body_h   = max(abs(c - o), (h - l) * 0.005)   # 最低でも値動きの0.5%
        face  = 'white' if c >= o else 'black'
        rect  = Rectangle(
            (i - 0.38, body_bot), 0.76, body_h,
            facecolor=face, edgecolor='black', linewidth=0.5, zorder=2
        )
        ax.add_patch(rect)
        ax.plot([i, i], [l, body_bot],          color='black', lw=0.6, zorder=1)
        ax.plot([i, i], [body_bot + body_h, h], color='black', lw=0.6, zorder=1)


def draw_volume_bars(ax, df):
    """出来高を白黒バーで描画する"""
    for i, (_, row) in enumerate(df.iterrows()):
        if pd.isna(row['Volume']) or pd.isna(row['Close']) or pd.isna(row['Open']):
            continue
        face = 'white' if float(row['Close']) >= float(row['Open']) else '#333333'
        ax.bar(i, float(row['Volume']),
               color=face, edgecolor='#888888', linewidth=0.3, width=0.8, zorder=1)


def _delta_abs(strikes, net_gex, prev_map, max_abs):
    """各ストライクの Δ|netGEX| を当日 max_abs で正規化して返す。

    厚み（＝壁の強さ）は符号ではなく絶対値なので、Put Wall が
    -2.28B → -2.73B と深まる場合も「厚くなった（正の Δ）」として扱う。
    比ではなく差なので、ゼロ除算も符号反転による発散も起きない。

    戻り値: (deltas, is_new) — is_new は前日に存在しなかったストライク。
    """
    n = len(strikes)
    if not prev_map or max_abs <= 0:
        return np.zeros(n), np.zeros(n, dtype=bool)

    deltas = np.zeros(n)
    is_new = np.zeros(n, dtype=bool)
    for i, (k, v) in enumerate(zip(strikes, net_gex)):
        prev = prev_map.get(float(k))
        if prev is None:
            # 前日に無かったストライク＝全額が増分
            deltas[i] = abs(v) / max_abs
            is_new[i] = abs(v) > 0
        else:
            deltas[i] = (abs(v) - abs(prev)) / max_abs
    return deltas, is_new


def _delta_color(delta):
    """Δ|netGEX|（正規化済み）→ (色, alpha)。

    +0.15 以上で朱に振り切る程度のスケール。閾値ではなく連続的に効かせる。
    """
    if delta is None or not np.isfinite(delta) or abs(delta) < 0.01:
        return INK, 0.82                      # ほぼ変化なし
    t = min(abs(float(delta)) / 0.15, 1.0)
    if delta > 0:
        return _blend(INK, CRIMSON, t), 0.82 + 0.10 * t   # 厚くなった
    return _blend(INK, GRAY, t), 0.82 - 0.32 * t          # 薄くなった


def _blend(c1, c2, t):
    """16進カラー c1→c2 を t(0..1) で線形補間する。"""
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return '#%02X%02X%02X' % tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def draw_gex_histogram(ax, profile, specific_levels, title_lines, y_min, y_max,
                       prev_map=None):
    """
    GEX ヒストグラム（横棒）を描画する。

    - バーは netGEX を各パネル内の最大絶対値で正規化（-1 〜 +1 スケール）
    - バーの色は**前日からの厚みの変化** Δ|netGEX| に連動（タスクB'）:
      厚くなった＝朱寄り / 薄くなった＝淡い墨 / 前日に無かったストライク＝縁取り。
      prev_map（{strike: netGEX}）が None のときは一律 INK（従来どおり）。
    - specific_levels に基づきパネル固有のレベル線を引く
    """
    ax.set_facecolor(CREAM)
    for sp in ax.spines.values():
        sp.set_color('#CCCCCC')
    ax.set_ylim(y_min, y_max)
    ax.yaxis.set_tick_params(labelleft=False, labelright=False)
    ax.set_xlim(-1.45, 1.45)
    ax.tick_params(axis='x', labelsize=7, colors='#888888')
    ax.set_xticks([-1, 0, 1])
    ax.set_xticklabels(['-max', '0', '+max'], fontsize=6, color='#888888')

    # ゼロライン
    ax.axvline(0, color='#AAAAAA', lw=0.8, zorder=1)

    if not profile:
        ax.text(0.5, 0.5, 'No data', transform=ax.transAxes,
                ha='center', va='center', color='#AAAAAA', fontsize=9)
    else:
        strikes = np.array([p['strike'] for p in profile], dtype=float)
        net_gex = np.array([p['netGEX'] for p in profile], dtype=float)

        # バー高さ: ストライク間隔の 80 %
        bar_h = (float(np.min(np.diff(np.sort(strikes)))) * 0.8
                 if len(strikes) > 1 else 1.0)

        # 正規化
        max_abs = float(np.max(np.abs(net_gex))) if net_gex.size > 0 else 1.0
        if max_abs == 0:
            max_abs = 1.0
        scaled = net_gex / max_abs

        # 前日比の厚み変化 Δ|netGEX|（色の強さの基準は当日の max_abs）
        deltas, is_new = _delta_abs(strikes, net_gex, prev_map, max_abs)

        for strike, val, dlt, new in zip(strikes, scaled, deltas, is_new):
            bar_color, alpha = _delta_color(dlt)
            ax.barh(strike, val, height=bar_h,
                    color=bar_color, alpha=alpha, zorder=2,
                    edgecolor=(AMBER if new else 'none'),
                    linewidth=(0.7 if new else 0))

        # 最大・最小 GEX のストライクにラベル
        idx_max = int(np.argmax(net_gex))
        idx_min = int(np.argmin(net_gex))
        if net_gex[idx_max] > 0:
            ax.text(scaled[idx_max] + 0.04, strikes[idx_max],
                    f'{net_gex[idx_max]/1e6:.0f}M',
                    va='center', ha='left', fontsize=6, color=INK, clip_on=True)
        if net_gex[idx_min] < 0:
            ax.text(scaled[idx_min] - 0.04, strikes[idx_min],
                    f'{net_gex[idx_min]/1e6:.0f}M',
                    va='center', ha='right', fontsize=6, color=INK, clip_on=True)

    # パネル固有のレベル線（短期・長期それぞれの HVL / Wall）
    if specific_levels:
        hvl = specific_levels.get('hvl')
        cw  = specific_levels.get('callWall')
        pw  = specific_levels.get('putWall')
        tz  = specific_levels.get('transition_zone')

        if tz and cw and pw:
            ax.axhspan(pw, cw, color=AMBER, alpha=0.07, zorder=0)  # Transition Zone

        if hvl:
            ax.axhline(hvl, color=INK, lw=1.5, ls='--', alpha=0.9, zorder=5)
            ax.text(1.4, hvl, f' HVL\n {hvl:.1f}',
                    va='bottom', ha='right', fontsize=6, color=INK, clip_on=True)
        if cw:
            ax.axhline(cw, color=GREEN, lw=1.2, ls='-', alpha=0.8, zorder=5)
            ax.text(1.4, cw, f' CW\n {cw:.1f}',
                    va='bottom', ha='right', fontsize=6, color=GREEN, clip_on=True)
        if pw:
            ax.axhline(pw, color=CRIMSON, lw=1.2, ls='-', alpha=0.8, zorder=5)
            ax.text(1.4, pw, f' PW\n {pw:.1f}',
                    va='top', ha='right', fontsize=6, color=CRIMSON, clip_on=True)

    ax.set_title('\n'.join(title_lines), fontsize=8, color=INK, pad=3)


def draw_connecting_line(fig, ax_st, ax_lt, hvl_st, hvl_lt):
    """
    短期 HVL と長期 HVL を繋ぐ 2段折れ線を描く。
    ConnectionPatch で ax_st の右端 → ax_lt の左端を結ぶ。
    """
    if hvl_st is None or hvl_lt is None:
        return
    try:
        con = ConnectionPatch(
            xyA=(1.45, hvl_st), coordsA='data', axesA=ax_st,
            xyB=(-1.45, hvl_lt), coordsB='data', axesB=ax_lt,
            color=INK, lw=1.4, ls='-', alpha=0.55, zorder=10,
            arrowstyle='-'
        )
        fig.add_artist(con)
    except Exception as e:
        logging.debug(f"ConnectionPatch skipped: {e}")


# ─────────────────────────────────────────────────────────────
# メインチャート作成
# ─────────────────────────────────────────────────────────────

DRAW_PROB_CONE = True   # タスク#13: 確率コーンの重ね描き
COLOR_BY_DELTA = True   # タスクB': GEXバーを前日比の厚み変化で色分け


def _cone_sigma(gex, symbol, date_str, span):
    """コーン描画用の σ を得る。

    第一候補は levels JSON の `probability`（3_extract_levels が同梱。
    クラウドでもローカルでも必ず存在する）。無い場合のみ IVアーカイブに落ちる
    （過去日を手元で再描画するときのため）。
    """
    prob = (gex or {}).get('probability') or {}
    sigmas = prob.get('sigma') or {}
    if sigmas:
        # span（営業日）に最も近いテナーのσを使う
        key = min(sigmas, key=lambda k: abs(int(k) - span))
        try:
            return float(sigmas[key])
        except (TypeError, ValueError):
            pass

    if not date_str:
        return None
    try:
        import iv_utils as _iv
    except ImportError:
        return None
    summary = _iv.load_summary(symbol, date_str)
    if not summary:
        return None
    return _iv.atm_iv_at_tenor(summary, max(7, int(span * 7 / 5)))


def _draw_prob_cone(ax, gex, symbol, date_str, spot, n_hist, x_text):
    """ローソク足パネルの未来領域に IVベースの確率コーンを重ねる.

    未来領域は 27営業日ぶん確保されているが、x_text 以降はレベル線のラベルが
    並ぶので、コーンはその手前までに収める（ラベルと重ねない）。
    y軸はローソク足と共有なので、CW/PW/HVL との位置関係がそのまま読める。
    """
    x_end = x_text - 1                 # ラベル開始の手前で止める
    span = max(1, x_end - n_hist)      # コーンの営業日数
    sigma = _cone_sigma(gex, symbol, date_str, span)
    if not sigma:
        return

    import numpy as _np
    x = _np.linspace(n_hist, x_end, 60)
    t = (x - n_hist) / 252.0           # 営業日 → 年
    band = spot * sigma * _np.sqrt(t)

    ax.fill_between(x, spot - 2 * band, spot + 2 * band,
                    color='#3B6EA5', alpha=0.10, lw=0, zorder=1,
                    clip_on=True)
    ax.fill_between(x, spot - band, spot + band,
                    color='#3B6EA5', alpha=0.16, lw=0, zorder=1,
                    clip_on=True)
    for edge in (band, -band):
        ax.plot(x, spot + edge, color='#3B6EA5', lw=0.7, alpha=0.55,
                zorder=1, clip_on=True)

    # 注記はコーンの下（未来領域の下側は空いている）。上端側はレベル線ラベルが混むため避ける
    y_lo, _ = ax.get_ylim()
    y_text = spot - 2 * band[-1] - (ax.get_ylim()[1] - y_lo) * 0.012
    # 左寄せ（右にはレベル線のラベル列があるので、そちらへ伸ばさない）
    ax.text(n_hist, max(y_text, y_lo),
            f'IV cone {sigma*100:.0f}%  ±1σ/±2σ',
            fontsize=7, color='#3B6EA5', ha='left', va='top',
            fontfamily='monospace', zorder=6, clip_on=True)


def create_chart(symbol, candle_limit=100):
    """1銘柄のローソク足 + GEX ヒストグラムチャートを作成して PNG に保存する"""

    gex = load_gex_levels(symbol)
    if gex is None:
        logging.warning(f"[{symbol}] GEX levels not found")
        return None

    ticker = yf.Ticker(symbol)
    raw = ticker.history(period="1y")

    # 未確定バーの除去。yfinance は当日分の行を OHLC が NaN のまま返すことがあり、
    # draw_candlesticks / draw_volume_bars はそれを黙ってスキップする一方で
    # n_hist = len(df) には数えてしまう。結果「枠はあるが足が描かれない」
    # （＝最新の1本が欠けたチャート）になるため、tail を取る前に落とす。
    ohlc = ['Open', 'High', 'Low', 'Close']
    clean = raw.dropna(subset=ohlc)
    dropped = len(raw) - len(clean)
    if dropped:
        logging.warning(
            f"[{symbol}] Dropped {dropped} incomplete price row(s) "
            f"(latest kept: {clean.index[-1].date() if len(clean) else 'none'})"
        )

    df = clean.tail(candle_limit)
    if df.empty:
        logging.warning(f"[{symbol}] No price data")
        return None
    df.index = df.index.tz_localize(None)

    # チャートの最終足と GEX データの日付がズレていたら警告（無言のズレを防ぐ）
    gex_date = gex.get('date')
    last_bar = str(df.index[-1].date())
    if gex_date and last_bar != gex_date:
        logging.warning(
            f"[{symbol}] Last candle {last_bar} != GEX date {gex_date} "
            f"(price data lagging)"
        )

    # 未来の 27 営業日を追加（レベル線の表示用、現在の 2/3 程度）
    n_hist = len(df)   # 履歴バー数（未来領域の開始インデックス）
    last_date = df.index[-1]
    future_dates = pd.bdate_range(start=last_date + pd.Timedelta(days=1), periods=27)
    df_future = pd.DataFrame(index=future_dates, columns=df.columns, dtype=float)
    df = pd.concat([df, df_future])
    n = len(df)

    spot    = gex['spotPrice']
    levels  = gex['levels']
    exp_info = gex.get('expirationInfo', {})
    total_gex = gex['totalGEX']

    # ── Y 軸範囲 ─────────────────────────────────────────────
    y_min = float(df['Low'].dropna().min())
    y_max = float(df['High'].dropna().max())

    for key in ['hvl', 'callWall', 'putWall']:
        v = levels.get(key)
        if v:
            y_min = min(y_min, float(v))
            y_max = max(y_max, float(v))
    for w in levels.get('callWalls', []) + levels.get('putWalls', []):
        y_min = min(y_min, float(w['strike']))
        y_max = max(y_max, float(w['strike']))

    y_pad  = (y_max - y_min) * 0.06
    y_min -= y_pad
    y_max += y_pad

    # ── Figure / GridSpec ────────────────────────────────────
    fig = plt.figure(figsize=(20, 10), facecolor=CREAM)
    gs  = gridspec.GridSpec(
        2, 3,
        width_ratios=[5.2, 0.9, 0.9],
        height_ratios=[4, 1],
        hspace=0.03, wspace=0.04,
        left=0.07, right=0.91, top=0.93, bottom=0.09
    )
    ax_c  = fig.add_subplot(gs[0, 0])              # ローソク足
    ax_v  = fig.add_subplot(gs[1, 0])              # 出来高
    ax_st = fig.add_subplot(gs[0, 1], sharey=ax_c) # 短期 GEX（ローソク足と同じ行・Y軸共有）
    ax_lt = fig.add_subplot(gs[0, 2], sharey=ax_c) # 長期 GEX（同上）
    # gs[1, 1] / gs[1, 2] は空白（右下）

    for ax in [ax_c, ax_v, ax_st, ax_lt]:
        ax.set_facecolor(CREAM)
        for sp in ax.spines.values():
            sp.set_color('#CCCCCC')

    # ── ローソク足 ───────────────────────────────────────────
    draw_candlesticks(ax_c, df)
    ax_c.set_xlim(-1, n)
    ax_c.set_ylim(y_min, y_max)
    ax_c.yaxis.set_major_formatter(mticker.FormatStrFormatter('%.0f'))
    ax_c.tick_params(axis='y', labelsize=8, colors=INK, left=True, right=False)
    ax_c.yaxis.set_label_position('left')
    ax_c.yaxis.tick_left()
    ax_c.set_xticks([])
    ax_c.grid(axis='y', color='#E0E0E0', lw=0.5, zorder=0)
    ax_c.set_title('All-term  (All Expirations)', fontsize=9, color=INK,
                   pad=4, loc='center', fontfamily='monospace')
    ax_st.grid(axis='y', color='#E0E0E0', lw=0.5, zorder=0)
    ax_lt.grid(axis='y', color='#E0E0E0', lw=0.5, zorder=0)

    # ── 出来高 ───────────────────────────────────────────────
    draw_volume_bars(ax_v, df)
    ax_v.set_xlim(-1, n)
    ax_v.yaxis.tick_right()
    ax_v.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f'{x/1e6:.0f}M' if x >= 1e6 else f'{x:.0f}')
    )
    ax_v.tick_params(axis='y', labelsize=6, colors='#888888')

    hist_len  = min(candle_limit, len(df))
    tick_step = max(1, hist_len // 8)
    ticks     = list(range(0, hist_len, tick_step))
    dates_all = df.index
    ax_v.set_xticks(ticks)
    ax_v.set_xticklabels(
        [dates_all[i].strftime('%m/%d') for i in ticks],
        rotation=45, fontsize=8, ha='right', color=INK
    )

    # ── ローソク足上の GEX レベル線（未来領域のみ描画） ────────
    # 未来領域を0起点とした場合の描画設定:
    #   day 0-1  : ローソクとの隙間
    #   day 2-10 : 横線描画
    #   day 13-  : テキストラベル（線なし、右端側に寄せて表示）
    _x_line_s = n_hist + 2        # 線の開始
    _x_line_e = n_hist + 10       # 線の終了
    _x_text   = n_hist + 13       # テキストの開始（右枠に寄せるため+2オフセット）

    _labels = []   # (price, text, color, fontsize, fontweight) 後でまとめて描画

    def _hline_candle(price, label, color, ls, lw):
        ax_c.plot([_x_line_s, _x_line_e], [price, price],
                  color=color, lw=lw, ls=ls, alpha=0.85, zorder=5)
        _labels.append((price, f'{label}: {price:.1f}', color, 8, 'normal'))

    # Call Walls（上位3本: 1本目は太く、2-3本目は細い破線）
    for i, w in enumerate(levels.get('callWalls', [])):
        lw = 1.8 if i == 0 else 1.0
        ls = '-'  if i == 0 else ':'
        label = 'Call' if i == 0 else f'CW{i+1}'
        _hline_candle(w['strike'], label, GREEN, ls, lw)

    # Put Walls（同上）
    for i, w in enumerate(levels.get('putWalls', [])):
        lw = 1.8 if i == 0 else 1.0
        ls = '-'  if i == 0 else ':'
        label = 'Put' if i == 0 else f'PW{i+1}'
        _hline_candle(w['strike'], label, CRIMSON, ls, lw)

    # HVL（未来領域のみ）
    if levels.get('hvl'):
        hvl_total = levels['hvl']
        ax_c.plot([_x_line_s, _x_line_e], [hvl_total, hvl_total],
                  color=INK, lw=1.8, ls='--', alpha=0.85, zorder=5)
        sentiment_arrow = '[+γ]' if spot > hvl_total else '[-γ]'
        _labels.append((hvl_total, f'HVL:{hvl_total:.1f} {sentiment_arrow}', INK, 8, 'normal'))

    # Spot price（未来領域のみ）
    ax_c.plot([_x_line_s, _x_line_e], [spot, spot],
              color=AMBER, lw=2.2, ls='-', alpha=0.95, zorder=6)
    _labels.append((spot, f'Spot:{spot:.2f}', AMBER, 9, 'bold'))

    # ── ラベルの重なり解消して描画 ─────────────────────────────
    # 価格が近いラベルを上下にずらす（最小間隔: y範囲の 1.5%）
    _min_gap = (y_max - y_min) * 0.015
    _sorted  = sorted(_labels, key=lambda x: x[0])
    _adj     = [x[0] for x in _sorted]
    # 下から上へ: 重なりを上へ押し上げ
    for i in range(1, len(_adj)):
        if _adj[i] - _adj[i - 1] < _min_gap:
            _adj[i] = _adj[i - 1] + _min_gap
    # 上から下へ: 押し上げすぎを均す
    for i in range(len(_adj) - 2, -1, -1):
        if _adj[i + 1] - _adj[i] < _min_gap:
            _adj[i] = _adj[i + 1] - _min_gap
    for (orig_p, text, color, fs, fw), ap in zip(_sorted, _adj):
        ax_c.text(_x_text, ap, text,
                  color=color, fontsize=fs, va='center', ha='left',
                  fontfamily='monospace', fontweight=fw,
                  clip_on=True, zorder=6)

    # Transition Zone（陰影）
    tz = levels.get('transition_zone')
    if tz:
        ax_c.axhspan(tz['lower'], tz['upper'],
                     color=AMBER, alpha=0.06, zorder=0, label='Transition Zone')

    # 確率コーン（タスク#13）: 未来領域にIVベースの±1σ/±2σを重ねる
    if DRAW_PROB_CONE:
        _draw_prob_cone(ax_c, gex, symbol, gex.get('date'), spot, n_hist, _x_text)

    # ── GEX ヒストグラム ─────────────────────────────────────
    st_exps = exp_info.get('shortTermExpirations', [])
    lt_exps = exp_info.get('longTermExpirations', [])

    st_title = (
        ['Short-term (DTE 0-7)'] +
        ([st_exps[-1]] if st_exps else ['(no data)'])
    )
    lt_end = max(lt_exps) if lt_exps else None
    lt_title = (
        ['Long-term (cumulative → SQ)'] +
        ([f'→ {lt_end}'] if lt_end else ['(no data)'])
    )

    # 前営業日の profile（タスクB': バーの色を前日比の厚み変化に連動させる）
    prev_profiles = load_prev_profiles(symbol, gex.get('date')) if COLOR_BY_DELTA else None
    if prev_profiles:
        logging.info(f"[{symbol}] delta coloring vs {prev_profiles.get('_date')}")

    draw_gex_histogram(
        ax_st, gex['profile']['short_term'],
        levels.get('short_term'), st_title, y_min, y_max,
        prev_map=(prev_profiles or {}).get('short_term')
    )
    draw_gex_histogram(
        ax_lt, gex['profile']['long_term'],
        levels.get('long_term'), lt_title, y_min, y_max,
        prev_map=(prev_profiles or {}).get('long_term')
    )

    # 長期パネルのY軸ラベルは非表示（左側ローソク足と共通スケールのため不要）
    ax_lt.tick_params(axis='y', labelright=False, right=False)

    # Spot ラインを GEX パネルにも表示
    ax_st.axhline(spot, color=AMBER, lw=1.5, ls='-', alpha=0.75, zorder=6)
    ax_lt.axhline(spot, color=AMBER, lw=1.5, ls='-', alpha=0.75, zorder=6)

    # ── 2段折れ線（ST HVL ↔ LT HVL） ────────────────────────
    hvl_st_val = (levels.get('short_term') or {}).get('hvl')
    hvl_lt_val = (levels.get('long_term')  or {}).get('hvl')
    draw_connecting_line(fig, ax_st, ax_lt, hvl_st_val, hvl_lt_val)

    # ── タイトル（シンボル名のみ） ────────────────────────────
    gex_str   = (f"{total_gex/1e9:.2f}B" if abs(total_gex) >= 1e9
                 else f"{total_gex/1e6:.0f}M")
    sent_str  = "Positive GEX" if gex['sentiment'] == 'positive_gamma' else "Negative GEX"
    sent_color = GREEN if gex['sentiment'] == 'positive_gamma' else CRIMSON

    call_w_str = (f"{levels['callWall']:.1f}" if levels.get('callWall') else 'N/A')
    put_w_str  = (f"{levels['putWall']:.1f}"  if levels.get('putWall')  else 'N/A')
    hvl_str    = (f"{levels['hvl']:.1f}"      if levels.get('hvl')      else 'N/A')
    spot_str   = f"{spot:.2f}"
    data_date  = gex.get('date', 'N/A')

    fig.suptitle(symbol, fontsize=18, color=INK,
                 fontfamily='monospace', fontweight='bold', y=0.978)

    # ── 右下情報パネル（gs[1, 1:3]） ────────────────────────────
    ax_info = fig.add_subplot(gs[1, 1:3])
    ax_info.set_facecolor(CREAM)
    for sp in ax_info.spines.values():
        sp.set_color('#CCCCCC')
    ax_info.set_xticks([])
    ax_info.set_yticks([])

    # ラベル列（左）と値列（右）を分けて描画。
    # バー色の凡例を出す日は、そのぶん右へ寄せて重なりを避ける。
    col_l = 0.58 if prev_profiles else 0.46   # ラベル開始 x（axes 座標）
    col_r = col_l + 0.04  # 値開始 x
    rows  = [0.85, 0.68, 0.50, 0.32, 0.14]   # 各行の y（axes 座標、上から）

    # γFilter 状況
    gamma_filter_passed = gex.get("gamma_filter_passed")
    gamma_filter_reason = gex.get("gamma_filter_reason", "")
    if gamma_filter_passed is True:
        if gamma_filter_reason == "etf_auto_retain":
            filter_str = "N/A (ETF)"
            filter_color = GRAY
        elif gamma_filter_reason == "always_include_skip":
            filter_str = "N/A (always)"
            filter_color = GRAY
        else:
            filter_str = "Passed"
            filter_color = GREEN
    elif gamma_filter_passed is False:
        filter_str = "Removed"
        filter_color = CRIMSON
    else:
        filter_str = "N/A"
        filter_color = GRAY

    labels_col = ['Data', 'GEX', 'HVL', 'Call / Put', 'γFilter']
    values_col = [
        data_date,
        f"{gex_str}  ({sent_str})",
        hvl_str,
        f"{call_w_str}  /  {put_w_str}",
        filter_str,
    ]
    value_colors = [INK, sent_color, INK, INK, filter_color]

    for y, lbl, val, vcol in zip(rows, labels_col, values_col, value_colors):
        ax_info.text(col_l, y, lbl, transform=ax_info.transAxes,
                     fontsize=8, color=GRAY, fontfamily='monospace',
                     va='center', ha='right')
        ax_info.text(col_r, y, val, transform=ax_info.transAxes,
                     fontsize=8, color=vcol, fontfamily='monospace',
                     va='center', ha='left', fontweight='bold')

    # バー色の凡例（前日比の厚み変化）。前日データが無い日は出さない。
    if prev_profiles:
        # 右ブロック（Data / GEX / HVL / Call-Put / γFilter）と同じ 5 行に揃える。
        # dx は凡例ブロック全体の右シフト量。パネル実寸 664px なので 1px ≒ 0.0015。
        dx = 0.07                         # 約46px
        ax_info.text(0.0275 + dx, rows[0], 'Bars vs prev',
                     transform=ax_info.transAxes, fontsize=7, color=GRAY,
                     fontfamily='monospace', va='center', ha='left')
        legend_rows = [
            (rows[1], _blend(INK, CRIMSON, 1.0), 'thicker'),
            (rows[2], INK,                       'unchanged'),
            (rows[3], _blend(INK, GRAY, 1.0),    'thinner'),
        ]
        for y, c, lbl in legend_rows:
            ax_info.plot([0.04 + dx, 0.10 + dx], [y, y],
                         transform=ax_info.transAxes,
                         color=c, lw=3.2, solid_capstyle='butt', clip_on=False)
            ax_info.text(0.12 + dx, y, lbl, transform=ax_info.transAxes,
                         fontsize=7, color=GRAY, fontfamily='monospace',
                         va='center', ha='left')
        rect_h = 0.06
        ax_info.add_patch(Rectangle(
            (0.04 + dx, rows[4] - rect_h / 2), 0.06, rect_h,
            transform=ax_info.transAxes,
            facecolor=_blend(INK, CRIMSON, 1.0), edgecolor=AMBER,
            linewidth=1.0, clip_on=False))
        ax_info.text(0.12 + dx, rows[4], 'new strike',
                     transform=ax_info.transAxes, fontsize=7, color=GRAY,
                     fontfamily='monospace', va='center', ha='left')

    # ── 保存 ─────────────────────────────────────────────────
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, f"{symbol}_gex.png")
    fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor=CREAM)
    plt.close(fig)

    logging.info(f"[{symbol}] Chart saved to {output_path}")
    return output_path


# ─────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────

SCREENER_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "screener_config.json"
)
OI_SURGE_CHART_TOP_N = 10   # OI急増銘柄はγ量上位N件のみチャート生成


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not os.path.exists(LEVELS_DIR):
        logging.error(f"Levels directory not found: {LEVELS_DIR}")
        return False

    all_json = [f for f in os.listdir(LEVELS_DIR) if f.endswith('.json')]
    if not all_json:
        logging.error("No level files found")
        return False

    # screener_config.json から always_include リストを取得
    always_include = []
    try:
        with open(SCREENER_CONFIG_PATH, 'r') as f:
            screener_cfg = json.load(f)
        always_include = screener_cfg.get("output", {}).get("always_include", [])
    except Exception as e:
        logging.warning(f"screener_config.json の読み込みに失敗（全銘柄をチャート生成）: {e}")

    # 全銘柄を levels JSON から読み込み
    all_levels = {}
    for fname in all_json:
        symbol = fname.replace('.json', '')
        path = os.path.join(LEVELS_DIR, fname)
        try:
            with open(path, 'r') as f:
                all_levels[symbol] = json.load(f)
        except Exception as e:
            logging.warning(f"[{symbol}] levels JSON 読み込みエラー: {e}")

    # always_include 銘柄は常にチャート生成
    # OI急増（non always_include）銘柄は |totalGEX| 降順で上位 OI_SURGE_CHART_TOP_N 件のみ
    oi_surge_symbols = [
        s for s in all_levels if s not in always_include
    ]
    oi_surge_symbols.sort(
        key=lambda s: abs(all_levels[s].get('totalGEX', 0)),
        reverse=True
    )
    oi_surge_to_chart = oi_surge_symbols[:OI_SURGE_CHART_TOP_N]

    symbols_to_chart = sorted(set(always_include) & set(all_levels)) + sorted(oi_surge_to_chart)

    logging.info(f"Always-include チャート対象: {sorted(set(always_include) & set(all_levels))}")
    logging.info(f"OI急増 γ量TOP{OI_SURGE_CHART_TOP_N} チャート対象: {sorted(oi_surge_to_chart)}")

    for symbol in symbols_to_chart:
        try:
            create_chart(symbol, candle_limit=100)
        except Exception as e:
            logging.error(f"[{symbol}] Chart error: {e}", exc_info=True)

    return True


if __name__ == "__main__":
    if main():
        sys.exit(0)
    else:
        sys.exit(1)
