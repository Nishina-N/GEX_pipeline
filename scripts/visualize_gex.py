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


def draw_gex_histogram(ax, profile, specific_levels, title_lines, y_min, y_max):
    """
    GEX ヒストグラム（横棒）を描画する。

    - バーは netGEX を各パネル内の最大絶対値で正規化（-1 〜 +1 スケール）
    - バーの色は IV 異常度に連動（現状は一律 INK; 将来対応）
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

        for strike, val in zip(strikes, scaled):
            # IV 異常度による色分け（将来: 墨→琥珀→朱）
            bar_color = INK
            ax.barh(strike, val, height=bar_h,
                    color=bar_color, alpha=0.82, edgecolor='none', zorder=2)

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

def create_chart(symbol, candle_limit=100):
    """1銘柄のローソク足 + GEX ヒストグラムチャートを作成して PNG に保存する"""

    gex = load_gex_levels(symbol)
    if gex is None:
        logging.warning(f"[{symbol}] GEX levels not found")
        return None

    ticker = yf.Ticker(symbol)
    df = ticker.history(period="1y").tail(candle_limit)
    if df.empty:
        logging.warning(f"[{symbol}] No price data")
        return None
    df.index = df.index.tz_localize(None)

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

    # ── GEX ヒストグラム ─────────────────────────────────────
    st_exps = exp_info.get('shortTermExpirations', [])
    lt_exps = exp_info.get('longTermExpirations', [])

    st_title = (
        ['Short-term (DTE 0-7)'] +
        ([st_exps[-1]] if st_exps else ['(no data)'])
    )
    lt_title = (
        ['Long-term (Monthly SQ)'] +
        ([', '.join(lt_exps)] if lt_exps else ['(no data)'])
    )

    draw_gex_histogram(
        ax_st, gex['profile']['short_term'],
        levels.get('short_term'), st_title, y_min, y_max
    )
    draw_gex_histogram(
        ax_lt, gex['profile']['long_term'],
        levels.get('long_term'), lt_title, y_min, y_max
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

    # ラベル列（左）と値列（右）を分けて描画
    col_l = 0.46   # ラベル開始 x（axes 座標）
    col_r = col_l + 0.04  # 値開始 x
    rows  = [0.80, 0.58, 0.36, 0.14]   # 各行の y（axes 座標、上から）

    labels_col = ['Data', 'GEX', 'HVL', 'Call / Put']
    values_col = [
        data_date,
        f"{gex_str}  ({sent_str})",
        hvl_str,
        f"{call_w_str}  /  {put_w_str}",
    ]
    value_colors = [INK, sent_color, INK, INK]

    for y, lbl, val, vcol in zip(rows, labels_col, values_col, value_colors):
        ax_info.text(col_l, y, lbl, transform=ax_info.transAxes,
                     fontsize=8, color=GRAY, fontfamily='monospace',
                     va='center', ha='right')
        ax_info.text(col_r, y, val, transform=ax_info.transAxes,
                     fontsize=8, color=vcol, fontfamily='monospace',
                     va='center', ha='left', fontweight='bold')

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

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not os.path.exists(LEVELS_DIR):
        logging.error(f"Levels directory not found: {LEVELS_DIR}")
        return False

    symbols = [f.replace('.json', '') for f in os.listdir(LEVELS_DIR) if f.endswith('.json')]
    if not symbols:
        logging.error("No level files found")
        return False

    logging.info(f"Creating charts for: {sorted(symbols)}")

    for symbol in sorted(symbols):
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
