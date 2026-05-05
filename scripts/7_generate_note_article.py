"""
7_generate_note_article.py

5銘柄（SPY/QQQ/SMH/IWM/NVDA）のGEXデータを読み込み、
Claude APIで日本語記事を生成して note-article/ に出力する。
"""

import os
import sys
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import anthropic

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# TARGET_SYMBOLS は動的に取得するため削除
GEX_DIR = Path("data/r2/gex/daily")
CHART_DIR_ROOT = Path("charts")
OUTPUT_DIR = Path("note-article")
MODEL = "claude-sonnet-4-6"


def get_previous_market_day(date_str: str) -> str | None:
    """
    指定日の前営業日を取得する。
    土日と主要祝日をスキップ。R2にデータが存在するかは呼び出し元で確認。
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
        logging.info(f"Loaded {len(symbols)} symbols from {symbols_file}")
        return symbols
    except Exception as e:
        logging.warning(f"Error loading symbols file: {e}. Using defaults.")
        return default_symbols


def load_gex_data(date_str: str, symbols: list[str]) -> dict[str, dict]:
    """指定日付の指定銘柄GEXデータを読み込む"""
    data = {}
    day_dir = GEX_DIR / date_str
    if not day_dir.exists():
        logging.error(f"GEX directory not found: {day_dir}")
        sys.exit(1)

    for symbol in symbols:
        path = day_dir / f"{symbol}.json"
        if not path.exists():
            logging.warning(f"[{symbol}] JSON not found: {path}")
            continue
        with open(path, encoding="utf-8") as f:
            data[symbol] = json.load(f)
        logging.info(f"[{symbol}] Loaded GEX data")

    return data


def format_gex_value(v: float) -> str:
    """GEX値を B/M 単位でフォーマット"""
    if abs(v) >= 1e9:
        return f"{v/1e9:+.2f}B"
    return f"{v/1e6:+.0f}M"


def load_comparison_data(today: str, symbols: list[str]) -> tuple[dict[str, dict], dict[str, dict] | None]:
    """
    当日と前日のGEXデータを読み込む。
    前日データが存在しない場合は None を返す。
    """
    today_data = load_gex_data(today, symbols)
    
    prev_day = get_previous_market_day(today)
    yesterday_data = None
    
    if prev_day:
        try:
            yesterday_data = load_gex_data(prev_day, symbols)
            if yesterday_data:
                logging.info(f"Loaded comparison data from {prev_day}")
            else:
                logging.warning(f"No data found for previous market day: {prev_day}")
        except Exception as e:
            logging.warning(f"Failed to load previous day data: {e}")
    
    return today_data, yesterday_data


def build_symbol_summary(symbol: str, d: dict) -> str:
    """1銘柄分のデータサマリーテキストを構築"""
    levels = d.get("levels", {})
    st = levels.get("short_term", {})
    lt = levels.get("long_term", {})

    def fmt(v):
        return f"{v:.2f}" if v else "N/A"

    def sentiment_ja(s):
        return "ポジティブγ（安定）" if s == "positive_gamma" else "ネガティブγ（不安定）"

    exp_info = d.get("expirationInfo", {})
    st_exps = exp_info.get("shortTermExpirations", [])
    lt_exps = exp_info.get("longTermExpirations", [])

    lines = [
        f"=== {symbol} ===",
        f"Spot: {d['spotPrice']:.2f}",
        f"Total GEX: {format_gex_value(d['totalGEX'])}",
        f"センチメント: {sentiment_ja(d['sentiment'])}",
        f"",
        f"[全期間]",
        f"  HVL: {fmt(levels.get('hvl'))}",
        f"  Call Wall: {fmt(levels.get('callWall'))}",
        f"  Put Wall: {fmt(levels.get('putWall'))}",
        f"  Transition Zone: {fmt(levels.get('transition_zone', {}).get('lower'))} - {fmt(levels.get('transition_zone', {}).get('upper'))}",
        f"",
        f"[短期 DTE0-7 | {', '.join(st_exps) if st_exps else 'N/A'}]",
        f"  HVL: {fmt(st.get('hvl'))}",
        f"  Call Wall: {fmt(st.get('callWall'))}",
        f"  Put Wall: {fmt(st.get('putWall'))}",
        f"  センチメント: {sentiment_ja(st.get('sentiment', ''))}",
        f"",
        f"[長期 月次SQ | {', '.join(lt_exps) if lt_exps else 'N/A'}]",
        f"  HVL: {fmt(lt.get('hvl'))}",
        f"  Call Wall: {fmt(lt.get('callWall'))}",
        f"  Put Wall: {fmt(lt.get('putWall'))}",
        f"  センチメント: {sentiment_ja(lt.get('sentiment', ''))}",
    ]
    return "\n".join(lines)


def build_comparison_summary(symbol: str, today_data: dict, yesterday_data: dict | None) -> str:
    """
    前日比較を含む銘柄サマリーを構築する。
    """
    base_summary = build_symbol_summary(symbol, today_data)
    
    if not yesterday_data or symbol not in yesterday_data:
        return base_summary + "\n[前日比較] データなし\n"
    
    prev = yesterday_data[symbol]
    today = today_data
    
    # 主要指標の変化を計算
    changes = []
    
    # Spot価格変化
    spot_change = today['spotPrice'] - prev['spotPrice']
    changes.append(f"Spot: {prev['spotPrice']:.2f} → {today['spotPrice']:.2f} ({spot_change:+.2f})")
    
    # GEX変化
    gex_change = today['totalGEX'] - prev['totalGEX']
    changes.append(f"GEX: {format_gex_value(prev['totalGEX'])} → {format_gex_value(today['totalGEX'])} ({format_gex_value(gex_change)})")
    
    # HVL変化
    today_hvl = today.get('levels', {}).get('hvl')
    prev_hvl = prev.get('levels', {}).get('hvl')
    if today_hvl and prev_hvl:
        hvl_change = today_hvl - prev_hvl
        changes.append(f"HVL: {prev_hvl:.2f} → {today_hvl:.2f} ({hvl_change:+.2f})")
    
    # センチメント変化
    today_sent = today.get('sentiment', 'unknown')
    prev_sent = prev.get('sentiment', 'unknown')
    if today_sent != prev_sent:
        changes.append(f"センチメント変化: {prev_sent} → {today_sent}")
    
    comparison_text = "\n[前日比較]\n" + "\n".join(f"  {change}" for change in changes)
    
    return base_summary + "\n" + comparison_text


def build_prompt(date_str: str, gex_data: dict[str, dict], yesterday_data: dict[str, dict] | None, chart_dir: str) -> str:
    symbols = list(gex_data.keys())
    
    # 当日データサマリー（前日比較含む）
    summaries = "\n\n".join(
        build_comparison_summary(sym, gex_data[sym], yesterday_data)
        for sym in symbols
        if sym in gex_data
    )
    
    # 前日データがある場合は比較分析の指示を追加
    comparison_instruction = ""
    if yesterday_data:
        prev_day = get_previous_market_day(date_str)
        comparison_instruction = f"""

# 前日比較データ（{prev_day}）

前日からの主要な変化を分析し、記事に反映してください。特に以下の点に注目：
- GEX・HVL・Wall レベルの変化とその意味
- センチメント（ポジティブγ・ネガティブγ）の変化
- 市場構造の変化（安定 → 不安定、またはその逆）
"""

    return f"""あなたはオプショントレーダー向けのGEX（ガンマ・エクスポージャー）レポートを執筆するアナリストです。

以下のGEXデータをもとに、note.com投稿用の日本語記事を生成してください。

# 本日のデータ（{date_str}）

{summaries}{comparison_instruction}

# 記事の要件

1. **対象読者**: オプションの基本知識があるトレーダー。概念の基礎説明は最小限にとどめること。
2. **文体**: 簡潔・客観的。推奨トレードや投資助言は一切含めない。
3. **構成**（この順番で）:
   - タイトル行（H1）: 「【GEXレポート】{date_str} — " + 対象銘柄名をスペース区切りで追記
   - ## 本日のTopics（80字程度）: 前日比較で最も注目すべき変化を1-2点で簡潔に記載
   - ## 今日の市場サマリー（200字程度）: 全体のセンチメント傾向と注目点を簡潔に
   - ## 銘柄別GEX一覧: Markdownの箇条書きで1銘柄1行にまとめること（下記フォーマット例に従うこと）
     ```
     - **SPY**　＋γ（安定）｜Spot 560.12｜GEX +1.38B｜HVL 558.00｜CW 570.00｜PW 545.00
     - **QQQ**　−γ（不安定）｜Spot 472.30｜GEX -421M｜HVL 475.00｜CW 480.00｜PW 460.00
     ```
     - Markdownの `|---|` 形式テーブルは**使用しないこと**
     - KaTeX（`$$`）・HTMLタグは**使用しないこと**
     - 対象銘柄すべての行を含めること
     - 区切りは全角縦棒（｜）を使うこと
     - 箇条書きの直後に以下の用語説明を**そのまま**挿入すること（変更・省略不可）:
       > Spot: 現在価格　／　GEX: ガンマエクスポージャー合計　／　HVL: 高ボラティリティレベル（GEXゼロクロス点）　／　CW: コールウォール（上値抵抗）　／　PW: プットウォール（下値支持）　／　＋γ: ポジティブガンマ（安定圏）　／　−γ: ネガティブガンマ（不安定圏）
   - 各銘柄の詳細セクション（対象銘柄順）:
     - 先頭に必ず画像マーカー: `![SPY]({chart_dir}/SPY_gex.png)` のようにSYMBOL部分を実際の銘柄名に置換して記載
     - スポット価格とHVLの位置関係（HVL上 or 下）
     - Transition Zone（Call Wall〜Put Wall）の意味
     - 短期と長期のHVL・Wall比較で見えること
     - 前日からの変化があれば必ず言及すること
   - ## 前日からの変化: 主要な変化点を2-3銘柄でハイライト（前日データがない場合はこのセクションを省略）
   - ## まとめ: 全体の相場環境を2〜3文で締める
   - ## 注記: 「本記事はGEXデータに基づく分析であり、GEX計算にはBAW（Barone-Adesi Whaley）モデルを使用しています。本記事はAIの補助を用いて作成しており、投資の推奨や助言ではありません。」

4. **数値の扱い**: 必ずデータの数値をそのまま使用すること。四捨五入はOK（小数点1〜2桁）。
5. **GEX合計の表記**: +/-を明示（例: +1.38B、-421M）。
6. **環境（センチメント）の表記**: 「＋γ（安定）」「−γ（不安定）」と表記。
7. 記事はMarkdown形式で出力すること。表・一覧はMarkdownテーブル・KaTeX・HTMLタグを使わず、**箇条書き（`-`）**で記述すること。

# 出力形式

以下のJSON形式で返答してください:

```json
{{
  "title": "【GEXレポート】{date_str} — " + 対象銘柄名,
  "tags": ["GEX", "ガンマエクスポージャー", "オプション"] + 対象銘柄タグ + ["相場分析"],
  "body": "（Markdown形式の記事全文）"
}}
```
"""


def generate_article(prompt: str) -> dict:
    """Claude APIで記事を生成"""
    client = anthropic.Anthropic()

    logging.info(f"Calling Claude API ({MODEL})...")
    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    # JSONブロックの抽出
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logging.error(f"JSON parse error: {e}")
        logging.error(f"Raw response (first 500 chars): {raw[:500]}")
        sys.exit(1)


def main():
    today = date.today().isoformat()
    
    # 銘柄リスト取得
    symbols = get_target_symbols()
    logging.info(f"Target symbols: {symbols}")

    # GEXデータ読み込み（前日比較含む）
    gex_data, yesterday_data = load_comparison_data(today, symbols)
    if not gex_data:
        logging.error("No GEX data found")
        sys.exit(1)
    
    logging.info(f"Loaded data for {len(gex_data)} symbols")
    if yesterday_data:
        logging.info(f"Comparison data available for {len(yesterday_data)} symbols")
    else:
        logging.info("No previous day data available")

    # チャートディレクトリ（GitHubにcommit済みの charts/{date}/）
    chart_dir = str(CHART_DIR_ROOT / today)

    # プロンプト構築（前日比較含む）
    prompt = build_prompt(today, gex_data, yesterday_data, chart_dir)

    # 記事生成
    try:
        result = generate_article(prompt)
    except Exception as e:
        logging.error(f"Article generation failed: {e}")
        sys.exit(1)

    # 出力
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    article_path = OUTPUT_DIR / "article.md"
    article_path.write_text(result["body"], encoding="utf-8")
    logging.info(f"Article saved to {article_path} ({len(result['body'])} chars)")

    meta = {
        "title": result["title"],
        "tags": result["tags"],
        "date": today,
        "symbols": symbols,
        "chart_dir": chart_dir,
        "has_comparison_data": yesterday_data is not None,
    }
    meta_path = OUTPUT_DIR / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"Meta saved to {meta_path}")
    
    logging.info("Article generation completed successfully")
    return True


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logging.info("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)
