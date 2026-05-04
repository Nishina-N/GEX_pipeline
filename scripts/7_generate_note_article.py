"""
7_generate_note_article.py

5銘柄（SPY/QQQ/SMH/IWM/NVDA）のGEXデータを読み込み、
Claude APIで日本語記事を生成して note-article/ に出力する。
"""

import os
import sys
import json
import logging
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import anthropic

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TARGET_SYMBOLS = ["SPY", "QQQ", "SMH", "IWM", "NVDA"]
GEX_DIR = Path("data/r2/gex/daily")
CHART_DIR_ROOT = Path("charts")
OUTPUT_DIR = Path("note-article")
MODEL = "claude-sonnet-4-6"


def load_gex_data(date_str: str) -> dict[str, dict]:
    """指定日付の5銘柄GEXデータを読み込む"""
    data = {}
    day_dir = GEX_DIR / date_str
    if not day_dir.exists():
        logging.error(f"GEX directory not found: {day_dir}")
        sys.exit(1)

    for symbol in TARGET_SYMBOLS:
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


def build_prompt(date_str: str, gex_data: dict[str, dict], chart_dir: str) -> str:
    summaries = "\n\n".join(
        build_symbol_summary(sym, gex_data[sym])
        for sym in TARGET_SYMBOLS
        if sym in gex_data
    )

    return f"""あなたはオプショントレーダー向けのGEX（ガンマ・エクスポージャー）レポートを執筆するアナリストです。

以下のGEXデータをもとに、note.com投稿用の日本語記事を生成してください。

# データ（{date_str}）

{summaries}

# 記事の要件

1. **対象読者**: オプションの基本知識があるトレーダー。概念の基礎説明は最小限にとどめること。
2. **文体**: 簡潔・客観的。推奨トレードや投資助言は一切含めない。
3. **構成**（この順番で）:
   - タイトル行（H1）: 「【GEXレポート】{date_str} — SPY・QQQ・SMH・IWM・NVDA」
   - ## 今日の市場サマリー（200字程度）: 全体のセンチメント傾向と注目点を簡潔に
   - ## 銘柄別GEX一覧: Markdownの箇条書きで1銘柄1行にまとめること（下記フォーマット例に従うこと）
     ```
     - **SPY**　＋γ（安定）｜Spot 560.12｜GEX +1.38B｜HVL 558.00｜CW 570.00｜PW 545.00
     - **QQQ**　−γ（不安定）｜Spot 472.30｜GEX -421M｜HVL 475.00｜CW 480.00｜PW 460.00
     ```
     - Markdownの `|---|` 形式テーブルは**使用しないこと**
     - KaTeX（`$$`）・HTMLタグは**使用しないこと**
     - 5銘柄すべての行を含めること
     - 区切りは全角縦棒（｜）を使うこと
     - 箇条書きの直後に以下の用語説明を**そのまま**挿入すること（変更・省略不可）:
       > Spot: 現在価格　／　GEX: ガンマエクスポージャー合計　／　HVL: 高ボラティリティレベル（GEXゼロクロス点）　／　CW: コールウォール（上値抵抗）　／　PW: プットウォール（下値支持）　／　＋γ: ポジティブガンマ（安定圏）　／　−γ: ネガティブガンマ（不安定圏）
   - 各銘柄の詳細セクション（## SPY, ## QQQ, ## SMH, ## IWM, ## NVDAの順）:
     - 先頭に必ず画像マーカー: `![SPY]({chart_dir}/SPY_gex.png)` のようにSYMBOL部分を実際の銘柄名に置換して記載
     - スポット価格とHVLの位置関係（HVL上 or 下）
     - Transition Zone（Call Wall〜Put Wall）の意味
     - 短期と長期のHVL・Wall比較で見えること
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
  "title": "【GEXレポート】{date_str} — SPY・QQQ・SMH・IWM・NVDA",
  "tags": ["GEX", "ガンマエクスポージャー", "オプション", "SPY", "QQQ", "SMH", "IWM", "NVDA", "相場分析"],
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

    # GEXデータ読み込み
    gex_data = load_gex_data(today)
    if not gex_data:
        logging.error("No GEX data found")
        sys.exit(1)

    # チャートディレクトリ（GitHubにcommit済みの charts/{date}/）
    chart_dir = str(CHART_DIR_ROOT / today)

    # プロンプト構築
    prompt = build_prompt(today, gex_data, chart_dir)

    # 記事生成
    result = generate_article(prompt)

    # 出力
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    article_path = OUTPUT_DIR / "article.md"
    article_path.write_text(result["body"], encoding="utf-8")
    logging.info(f"Article saved to {article_path} ({len(result['body'])} chars)")

    meta = {
        "title": result["title"],
        "tags": result["tags"],
        "date": today,
        "chart_dir": chart_dir,
    }
    meta_path = OUTPUT_DIR / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"Meta saved to {meta_path}")


if __name__ == "__main__":
    main()
