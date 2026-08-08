"""IVサマリの生成（満期別 ATM IV・25Δスキュー）.

もとは `5_upload_to_r2.py` 内の `_build_iv_summary()` だったが、
**アップロード（step5）より前にサマリが要る**ようになったため切り出した。

呼び出し順の都合:
    step1 fetch → data/iv_history/{symbol}/{date}.pkl を生成
    step3 extract_levels → 本モジュールでサマリを作り levels JSON に確率情報を載せる
    step5 upload → 生成済みサマリをそのままR2へ

サマリの保存先: `data/iv_history/{symbol}/{date}_iv_summary.json`
"""
import json
import logging
import os
import pickle

import pandas as pd

DATA_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
IV_HIST_DIR = os.path.join(DATA_FOLDER, "iv_history")
OPTIONS_DIR = os.path.join(DATA_FOLDER, "options")


def summary_path(symbol: str, date_str: str) -> str:
    return os.path.join(IV_HIST_DIR, symbol, f"{date_str}_iv_summary.json")


def build_iv_summary(symbol, date_str, iv_df, spot_price):
    """IV DataFrame から満期別のサマリ（ATM IV・25Δスキュー等）を生成する。

    25Δ近似: ATMストライクから ±σ√T 程度のストライクを参照。
    簡略化として上位/下位 25% 分位のストライクを使用。
    """
    summary = {
        'date': date_str,
        'symbol': symbol,
        'spotPrice': spot_price,
        'expirations': []
    }

    if iv_df is None or iv_df.empty:
        return summary

    today = pd.Timestamp(date_str)

    for exp, grp in iv_df.groupby('expiration'):
        exp_date = pd.Timestamp(exp)
        dte = max(0, (exp_date - today).days)

        # ATM: spot に最も近いストライク
        grp = grp.copy()
        grp['dist'] = (grp['strike'] - spot_price).abs()
        atm_row = grp.loc[grp['dist'].idxmin()]
        atm_iv = float(atm_row['impliedVolatility']) if pd.notna(atm_row['impliedVolatility']) else None

        # 25Δ put/call 近似: put は ATM より低い上位25%ile、call は高い上位25%ile
        puts = grp[grp['optionType'] == 'put'].copy()
        calls = grp[grp['optionType'] == 'call'].copy()

        put_25d_iv, call_25d_iv, skew = None, None, None
        if not puts.empty and not calls.empty:
            put_low_strikes = puts[puts['strike'] <= spot_price]
            if not put_low_strikes.empty:
                q25_put = put_low_strikes['strike'].quantile(0.75)  # 下から75% = ATMに近い25Δ側
                row = put_low_strikes.iloc[(put_low_strikes['strike'] - q25_put).abs().argsort()[:1]]
                put_25d_iv = float(row['impliedVolatility'].values[0]) if pd.notna(row['impliedVolatility'].values[0]) else None

            call_high_strikes = calls[calls['strike'] >= spot_price]
            if not call_high_strikes.empty:
                q75_call = call_high_strikes['strike'].quantile(0.25)
                row = call_high_strikes.iloc[(call_high_strikes['strike'] - q75_call).abs().argsort()[:1]]
                call_25d_iv = float(row['impliedVolatility'].values[0]) if pd.notna(row['impliedVolatility'].values[0]) else None

            if put_25d_iv is not None and call_25d_iv is not None:
                skew = round(put_25d_iv - call_25d_iv, 4)

        summary['expirations'].append({
            'expiration': exp,
            'dte': dte,
            'atm_iv': round(atm_iv, 4) if atm_iv is not None else None,
            'put_25d_iv': round(put_25d_iv, 4) if put_25d_iv is not None else None,
            'call_25d_iv': round(call_25d_iv, 4) if call_25d_iv is not None else None,
            'skew': skew,
        })

    summary['expirations'].sort(key=lambda x: x['dte'])
    return summary


def build_and_save(symbol: str, date_str: str) -> dict | None:
    """当日の IV pkl からサマリを生成して JSON に保存し、その内容を返す。

    既に保存済みならそれを読んで返す（step3 と step5 の二重生成を避ける）。
    """
    out = summary_path(symbol, date_str)
    if os.path.exists(out):
        try:
            with open(out, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass  # 壊れていたら作り直す

    pkl_path = os.path.join(IV_HIST_DIR, symbol, f"{date_str}.pkl")
    if not os.path.exists(pkl_path):
        return None

    try:
        with open(pkl_path, 'rb') as f:
            iv_df = pickle.load(f)

        spot_price = None
        opt_path = os.path.join(OPTIONS_DIR, f"{symbol}.pkl")
        if os.path.exists(opt_path):
            with open(opt_path, 'rb') as f:
                spot_price = pickle.load(f).get('spot_price')

        summary = build_iv_summary(symbol, date_str, iv_df, spot_price)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False)
        return summary
    except Exception as e:
        logging.warning(f"[{symbol}] IV summary build failed: {e}")
        return None
