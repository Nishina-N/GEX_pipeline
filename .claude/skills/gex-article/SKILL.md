---
name: gex-article
description: ローカルのGEX levels JSONから note.com向けGEXレポート記事(指数→M7→OI急増の3部構成)を生成し、Obsidian の fleet note に保存する。ユーザーが「GEX記事を作って」「GEXレポートを生成」等と依頼したときに使う。
---

# GEX レポート記事生成スキル

R2 から取得済みの GEX データをもとに、note.com 投稿用の日本語記事を生成し、
Obsidian Vault に保存する。改善要望はこの SKILL.md を編集することで反映する。

## 前提・入力

1. **データ取得が先**: `python scripts/pull_from_r2.py [--date YYYY-MM-DD]` を実行済みであること。
   未実行なら先に実行する（既定で R2 最新日付を取得）。
2. **入力ファイル**:
   - 当日 levels: `data/r2/gex/daily/{date}/{symbol}.json`
   - 前日 levels: `data/r2/gex/daily/{prev}/{symbol}.json`（前日比較用。無ければ比較省略）
   - チャート: `data/r2/charts/{date}/{date}_{symbol}_gex.png`
3. **解釈規範**: `GEX_CLAUDE_GUIDELINE.md` を必ず読み、GEXの解釈（totalGEXとCall Wallの独立性、
   sentimentはSpot vs HVLで決まる等）を厳守する。

## 銘柄グループ

- **指数**: `SPY, QQQ, IWM, SMH`
- **M7**: `AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA`
- **OI急増**: 当日 levels に存在する銘柄から「指数・M7・ETF(`SPY,QQQ,SMH,DIA,IWM`)」を除いた残りを
  `|totalGEX|` 降順で上位 **5件**。

各銘柄の levels JSON の構造は `GEX_CLAUDE_GUIDELINE.md` 付録および `3_extract_levels.py` 参照
（`spotPrice`, `totalGEX`, `sentiment`, `levels.{hvl,callWall,putWall,transition_zone,short_term,long_term}`,
`expirationInfo` 等）。

## 出力先（Obsidian）

- 記事: `C:\Users\nishiha\Work\Obsidian\Obsidian Vault R2\fleet note\GEX_{date}.md`
- 画像: `C:\Users\nishiha\Work\Obsidian\Obsidian Vault R2\fleet note\attachments\{date}\` に
  `data/r2/charts/{date}/*.png` をコピーする。
- 記事内の画像は **標準Markdown相対リンク**で記述:
  `![SPY](attachments/{date}/{date}_SPY_gex.png)`（Obsidianでも note投稿スクリプトでも解釈可能）。

### フロントマター（記事冒頭）

```yaml
---
title: 【GEXレポート】{date} — {全掲載銘柄スペース区切り}
tags: [GEX, ガンマエクスポージャー, オプション, {各銘柄}, 相場分析]
date: {date}
status: draft
---
```

## 記事構成（この順番）

### 冒頭（全体）
- **# 【GEXレポート】{date} — {全掲載銘柄}**（H1）
- **## 本日のTopics**（80字程度）: 前日比較で最も注目すべき変化を1〜2点。
- **## 今日の市場サマリー**（200字程度）: 指数・M7全体のセンチメント傾向と注目点。

### Part 1: 指数分析（SPY, QQQ, IWM, SMH）
- **## 指数別GEX一覧**: 箇条書き1銘柄1行。フォーマット:
  `- **SPY**　＋γ（安定）｜Spot 560.12｜GEX +1.38B｜HVL 558.00｜CW 570.00｜PW 545.00`
  - 区切りは全角縦棒（｜）。テーブル・KaTeX・HTMLタグは使用しない。
  - GEX表記は +/- 明示（例 +1.38B、-421M）。センチメントは「＋γ（安定）」「−γ（不安定）」。
  - 箇条書き直後に**用語説明ブロックをそのまま**挿入（変更・省略不可、下記「用語説明」）。
- **各指数の詳細セクション**（### {SYMBOL} 詳細）:
  - 先頭に画像: `![SPY](attachments/{date}/{date}_SPY_gex.png)`
  - SpotとHVLの位置関係 / Transition Zone（Call Wall〜Put Wall）の意味 /
    短期・長期のHVL・Wall比較 / 前日からの変化（あれば必ず言及）。

### Part 2: M7分析（AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA）
- **## M7銘柄のGEX一覧**: 指数一覧と同じフォーマット。直後に同じ用語説明を挿入。
- **各M7銘柄の詳細セクション**: 指数と同等の詳細（Spot/HVL・Transition Zone・短期/長期・前日変化）。

### Part 3: 注目のOI急増銘柄
- ※OI急増銘柄が無ければこのセクションを省略。
- **## 注目のOI急増銘柄**: 冒頭1〜2文で背景説明（OI急増とは何か・なぜ注目するか）。
  各銘柄を1行で紹介（指数一覧と同じフォーマット）。直後に同じ用語説明を挿入。
- **各OI急増銘柄の詳細セクション**: 画像＋2〜3文の簡易コメント（センチメント・SpotとHVLの位置・Wall）。
  詳細な時系列分析や前日比較は不要。

### 共通フッター
- **## 前日からの変化**（前日データあり時のみ）: 指数・M7の主要変化を2〜3銘柄でハイライト。
- **## まとめ**: 全体の相場環境を2〜3文。
- **## 注記**: 「本記事はGEXデータに基づく分析であり、GEX計算にはBAW（Barone-Adesi Whaley）モデルを使用しています。本記事はAIの補助を用いて作成しており、投資の推奨や助言ではありません。」

## 用語説明（各GEX一覧の直後に毎回そのまま挿入）

> Spot: 現在価格　／　GEX: ガンマエクスポージャー合計　／　HVL: 高ボラティリティレベル（GEXゼロクロス点）　／　CW: コールウォール（上値抵抗）　／　PW: プットウォール（下値支持）　／　＋γ: ポジティブガンマ（安定圏）　／　−γ: ネガティブガンマ（不安定圏）

## 文体・ルール

- 対象読者: オプションの基本知識があるトレーダー。基礎説明は最小限。
- 簡潔・客観的。推奨トレードや投資助言は含めない。
- 数値はデータの値をそのまま使用（小数1〜2桁、四捨五入可）。
- Markdown形式。テーブル・KaTeX・HTMLタグ不使用、箇条書き（`-`）で記述。
- GEX値のフォーマット: 絶対値 ≥ 1e9 は `{x/1e9:+.2f}B`、それ未満は `{x/1e6:+.0f}M`。

## 手順（実行時）

1. 対象 `{date}` を決定（`data/r2/gex/daily/` の最新ディレクトリ。無ければ `pull_from_r2.py` を実行）。
2. `GEX_CLAUDE_GUIDELINE.md` を読む。
3. 当日・前日の levels JSON を読み込み、グループ分け（指数・M7・OI急増top5）を確定。
4. 記事本文を上記構成で生成。
5. `attachments/{date}/` を作成し、`data/r2/charts/{date}/*.png` のうち掲載銘柄分をコピー。
6. フロントマター付きで `fleet note/GEX_{date}.md` に保存。
7. 保存先パスをユーザーに伝え、確認・改善要望を促す。
