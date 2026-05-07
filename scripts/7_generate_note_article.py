"""
7_generate_note_article.py

2部構成の記事を生成する:
  Part 1: コア5銘柄（SPY/QQQ/SMH/IWM/NVDA）― 前日比較あり・詳細分析
  Part 2: OIスクリーニング急増銘柄 TOP5 ― 簡易紹介
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

from market_calendar import get_previous_market_day

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# コア銘柄（常時詳細分析・前日比較対象）
CORE_SYMBOLS = ['SPY', 'QQQ', 'SMH', 'IWM', 'NVDA']

GEX_DIR = Path("data/r2/gex/daily")
CHART_DIR_ROOT = Path("charts")
OUTPUT_DIR = Path("note-article")
MODEL = "claude-sonnet-4-6"
OI_SURGE_TOP_N = 5  # OI急増銘柄は上位5件のみ
GUIDELINE_PATH = Path(__file__).parent.parent / "GEX_CLAUDE_GUIDELINE.md"


# ── 銘柄リスト取得 ──────────────────────────────────────────────────────────

def get_oi_surge_symbols(core_symbols: list[str], top_n: int = OI_SURGE_TOP_N) -> list[str]:
    """
    symbols_oi_surge.json からコア銘柄を除いた上位 top_n 件を返す。
    ファイルが存在しない場合は空リストを返す。
    """
    symbols_file = Path("data/symbols_oi_surge.json")

    if not symbols_file.exists():
        logging.warning(f"Symbols file not found: {symbols_file}. No OI surge symbols.")
        return []

    try:
        with open(symbols_file, encoding="utf-8") as f:
            data = json.load(f)
        # gamma フィルタ適用済みの symbols リストを参照（positive_gamma 銘柄のみ）
        all_symbols = data.get("symbols", [])
        surge = [s for s in all_symbols if s not in core_symbols][:top_n]
        if data.get("gamma_filter_applied"):
            removed = data.get("gamma_filter_removed", [])
            logging.info(f"Gamma filter was applied. Removed symbols: {removed}")
        logging.info(f"OI surge symbols (top {top_n}): {surge}")
        return surge
    except Exception as e:
        logging.warning(f"Error loading symbols file: {e}. No OI surge symbols.")
        return []


# ── GEXデータ読み込み ────────────────────────────────────────────────────────

def load_gex_data(date_str: str, symbols: list[str], optional: bool = False) -> dict[str, dict]:
    """指定日付の指定銘柄GEXデータを読み込む。optional=True なら欠損時に {} を返す。"""
    data = {}
    day_dir = GEX_DIR / date_str
    if not day_dir.exists():
        if optional:
            logging.warning(f"GEX directory not found (optional): {day_dir}")
            return {}
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


# ── テキスト構築ヘルパー ─────────────────────────────────────────────────────

def format_gex_value(v: float) -> str:
    if abs(v) >= 1e9:
        return f"{v/1e9:+.2f}B"
    return f"{v/1e6:+.0f}M"


def build_symbol_summary(symbol: str, d: dict) -> str:
    """コア銘柄の詳細サマリー（短期・長期・感応度まで含む）"""
    levels = d.get("levels") or {}
    st = levels.get("short_term") or {}
    lt = levels.get("long_term") or {}

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
        f"  Transition Zone: {fmt((levels.get('transition_zone') or {}).get('lower'))} - {fmt((levels.get('transition_zone') or {}).get('upper'))}",
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


def build_symbol_brief(symbol: str, d: dict) -> str:
    """OI急増銘柄の簡易1行サマリー（キーレベルのみ）"""
    levels = d.get("levels") or {}

    def fmt(v):
        return f"{v:.2f}" if v else "N/A"

    def sentiment_ja(s):
        return "＋γ（安定）" if s == "positive_gamma" else "−γ（不安定）"

    return (
        f"=== {symbol} ===\n"
        f"Spot: {d['spotPrice']:.2f} | センチメント: {sentiment_ja(d['sentiment'])} | "
        f"GEX: {format_gex_value(d['totalGEX'])} | HVL: {fmt(levels.get('hvl'))} | "
        f"CW: {fmt(levels.get('callWall'))} | PW: {fmt(levels.get('putWall'))}"
    )


def build_comparison_summary(symbol: str, today_data: dict, yesterday_data: dict | None) -> str:
    """前日比較を含むコア銘柄サマリー"""
    base_summary = build_symbol_summary(symbol, today_data)

    if not yesterday_data or symbol not in yesterday_data:
        return base_summary + "\n[前日比較] データなし\n"

    prev = yesterday_data[symbol]
    today = today_data
    changes = []

    spot_change = today['spotPrice'] - prev['spotPrice']
    changes.append(f"Spot: {prev['spotPrice']:.2f} → {today['spotPrice']:.2f} ({spot_change:+.2f})")

    gex_change = today['totalGEX'] - prev['totalGEX']
    changes.append(f"GEX: {format_gex_value(prev['totalGEX'])} → {format_gex_value(today['totalGEX'])} ({format_gex_value(gex_change)})")

    today_hvl = today.get('levels', {}).get('hvl')
    prev_hvl = prev.get('levels', {}).get('hvl')
    if today_hvl and prev_hvl:
        hvl_change = today_hvl - prev_hvl
        changes.append(f"HVL: {prev_hvl:.2f} → {today_hvl:.2f} ({hvl_change:+.2f})")

    today_sent = today.get('sentiment', 'unknown')
    prev_sent = prev.get('sentiment', 'unknown')
    if today_sent != prev_sent:
        changes.append(f"センチメント変化: {prev_sent} → {today_sent}")

    comparison_text = "\n[前日比較]\n" + "\n".join(f"  {c}" for c in changes)
    return base_summary + "\n" + comparison_text


# ── プロンプト構築 ────────────────────────────────────────────────────────────

def build_prompt(
    date_str: str,
    core_data: dict[str, dict],
    oi_surge_data: dict[str, dict],
    yesterday_data: dict[str, dict] | None,
    chart_dir: str,
) -> str:
    core_symbols = list(core_data.keys())
    surge_symbols = list(oi_surge_data.keys())

    # コア銘柄：詳細サマリー（前日比較あり）
    core_summaries = "\n\n".join(
        build_comparison_summary(sym, core_data[sym], yesterday_data)
        for sym in core_symbols
    )

    # OI急増銘柄：簡易サマリー
    surge_section = ""
    if surge_symbols:
        surge_lines = "\n\n".join(
            build_symbol_brief(sym, oi_surge_data[sym])
            for sym in surge_symbols
        )
        surge_section = f"""
# OI急増銘柄データ（スクリーニング上位{len(surge_symbols)}件）

{surge_lines}
"""

    # 前日比較の追加指示
    comparison_instruction = ""
    if yesterday_data:
        prev_day = get_previous_market_day(date_str)
        comparison_instruction = f"""
# 前日比較（{prev_day}）

コア銘柄について前日からの主要変化を分析し記事に反映してください:
- GEX・HVL・Wall レベルの変化とその意味
- センチメント（ポジティブγ・ネガティブγ）の変化
- 市場構造の変化（安定 → 不安定、またはその逆）
"""

    all_symbols_for_title = core_symbols + surge_symbols

    return f"""あなたはオプショントレーダー向けのGEX（ガンマ・エクスポージャー）レポートを執筆するアナリストです。

以下のGEXデータをもとに、note.com投稿用の日本語記事を生成してください。

# コア銘柄データ（{date_str}）

{core_summaries}{comparison_instruction}{surge_section}

# 記事の要件

1. **対象読者**: オプションの基本知識があるトレーダー。概念の基礎説明は最小限。
2. **文体**: 簡潔・客観的。推奨トレードや投資助言は一切含めない。
3. **構成**（この順番で）:

   ### Part 1: コア銘柄分析（{', '.join(core_symbols)}）

   - **タイトル行（H1）**: 「【GEXレポート】{date_str} — {' '.join(all_symbols_for_title)}」
   - **## 本日のTopics**（80字程度）: 前日比較で最も注目すべき変化を1〜2点で簡潔に
   - **## 今日の市場サマリー**（200字程度）: コア銘柄全体のセンチメント傾向と注目点
   - **## 銘柄別GEX一覧**（コア銘柄のみ）: 箇条書き1銘柄1行
     ```
     - **SPY**　＋γ（安定）｜Spot 560.12｜GEX +1.38B｜HVL 558.00｜CW 570.00｜PW 545.00
     - **QQQ**　−γ（不安定）｜Spot 472.30｜GEX -421M｜HVL 475.00｜CW 480.00｜PW 460.00
     ```
     - Markdownテーブル・KaTeX・HTMLタグは**使用しないこと**
     - 区切りは全角縦棒（｜）を使うこと
     - 箇条書きの直後に以下の用語説明を**そのまま**挿入すること（変更・省略不可）:
       > Spot: 現在価格　／　GEX: ガンマエクスポージャー合計　／　HVL: 高ボラティリティレベル（GEXゼロクロス点）　／　CW: コールウォール（上値抵抗）　／　PW: プットウォール（下値支持）　／　＋γ: ポジティブガンマ（安定圏）　／　−γ: ネガティブガンマ（不安定圏）
   - **各コア銘柄の詳細セクション**（銘柄順）:
     - 先頭に画像マーカー: `![SPY]({chart_dir}/SPY_gex.png)` のようにSYMBOL部分を実際の銘柄名に置換
     - スポット価格とHVLの位置関係
     - Transition Zone（Call Wall〜Put Wall）の意味
     - 短期と長期のHVL・Wall比較
     - 前日からの変化があれば必ず言及
   - **## 前日からの変化**（前日データあり時のみ）: 主要変化を2〜3銘柄でハイライト
   - **## まとめ**: 全体の相場環境を2〜3文で締める

   ### Part 2: 注目のOI急増銘柄（昨日のスクリーニング結果）

   ※ OI急増銘柄データが提供されている場合のみ記載。なければこのセクションを省略。

   - **## 注目のOI急増銘柄**（セクションヘッダー）
     - 冒頭1〜2文でスクリーニング背景を簡潔に説明（OI急増銘柄とは何か・なぜ注目するか）
     - 各銘柄を箇条書き1行で紹介（コア銘柄一覧と同じフォーマット）
     - 箇条書き直後に同じ用語説明を挿入（上記と同じもの）
   - **各OI急増銘柄の詳細セクション**（銘柄順）:
     - 先頭に画像マーカー: `![SPY]({chart_dir}/SPY_gex.png)` のようにSYMBOL部分を実際の銘柄名に置換
     - 銘柄ごとに2〜3文の簡易コメント（センチメント・HVLとSpotの位置関係・Wall情報）
     - 詳細な時系列分析や前日比較は不要


   ### 共通フッター

   - **## 注記**: 「本記事はGEXデータに基づく分析であり、GEX計算にはBAW（Barone-Adesi Whaley）モデルを使用しています。本記事はAIの補助を用いて作成しており、投資の推奨や助言ではありません。」

4. **数値**: データの数値をそのまま使用（四捨五入OK、小数点1〜2桁）。
5. **GEX表記**: +/-を明示（例: +1.38B、-421M）。
6. **センチメント表記**: 「＋γ（安定）」「−γ（不安定）」。
7. 記事はMarkdown形式。テーブル・KaTeX・HTMLタグ不使用、**箇条書き（`-`）**で記述。

# 出力形式

以下のJSON形式で返答してください:

```json
{{
  "title": "【GEXレポート】GEXからみた相場観と注目株 +{date_str}",
  "tags": ["GEX", "ガンマエクスポージャー", "オプション"] + 対象銘柄タグ + ["相場分析"],
  "body": "（Markdown形式の記事全文）"
}}
```
"""


# ── API呼び出し ──────────────────────────────────────────────────────────────

def load_guideline() -> str:
    """GEX_CLAUDE_GUIDELINE.md を読み込んでシステムプロンプトとして返す。"""
    if GUIDELINE_PATH.exists():
        guideline = GUIDELINE_PATH.read_text(encoding="utf-8")
        logging.info(f"Loaded GEX guideline ({len(guideline)} chars)")
        return guideline
    logging.warning(f"GEX_CLAUDE_GUIDELINE.md not found at {GUIDELINE_PATH}, proceeding without it")
    return ""


def generate_article(prompt: str) -> dict:
    client = anthropic.Anthropic()
    guideline = load_guideline()

    logging.info(f"Calling Claude API ({MODEL})...")
    create_kwargs = dict(
        model=MODEL,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )
    if guideline:
        create_kwargs["system"] = guideline

    message = client.messages.create(**create_kwargs)

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


# ── エントリーポイント ────────────────────────────────────────────────────────

def main():
    today = date.today().isoformat()

    # 銘柄リスト決定
    core_symbols = CORE_SYMBOLS
    oi_surge_symbols = get_oi_surge_symbols(core_symbols, top_n=OI_SURGE_TOP_N)
    logging.info(f"Core symbols: {core_symbols}")
    logging.info(f"OI surge symbols: {oi_surge_symbols}")

    # 当日GEXデータ読み込み
    core_data = load_gex_data(today, core_symbols)
    if not core_data:
        logging.error("No core GEX data found")
        sys.exit(1)
    logging.info(f"Loaded core data for {len(core_data)} symbols")

    oi_surge_data: dict[str, dict] = {}
    if oi_surge_symbols:
        oi_surge_data = load_gex_data(today, oi_surge_symbols, optional=True)
        logging.info(f"Loaded OI surge data for {len(oi_surge_data)} symbols")

    # 前日データ（コア銘柄のみ）
    yesterday_data: dict[str, dict] | None = None
    prev_day = get_previous_market_day(today)
    if prev_day:
        yesterday_data = load_gex_data(prev_day, core_symbols, optional=True)
        if yesterday_data:
            logging.info(f"Comparison data available for {len(yesterday_data)} core symbols ({prev_day})")
        else:
            logging.warning(f"No previous day data found: {prev_day}")
    else:
        logging.info("No previous day data available")

    # チャートディレクトリ
    chart_dir = str(CHART_DIR_ROOT / today)

    # プロンプト構築・記事生成
    prompt = build_prompt(today, core_data, oi_surge_data, yesterday_data, chart_dir)

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
        "core_symbols": core_symbols,
        "oi_surge_symbols": oi_surge_symbols,
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
