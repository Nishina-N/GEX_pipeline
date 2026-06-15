# ローカル記事生成への移行プラン

GEXプロファイル記事の生成を「GitHub Actions上の Python+API」から「ローカルの Claude Code スキル」へ移行する計画。改善（プロンプト調整）の反復を容易にし、Obsidian で確認・編集してから note へ下書き投稿する運用にする。

## 目標アーキテクチャ

```
┌─ クラウド / GitHub Actions（自動・毎営業日） ───────────────┐
│ step0-4: スクリーニング→取得→GEX計算→levels抽出→R2アップロード │
│ visualize: チャート描画                                       │
│ 追加     : チャートPNGもR2へアップロード                        │
│ 最終的に : step7(記事生成)・step8(note投稿)をCIから除去          │
└──────────────────────────────────────────────┘
                         │ R2（単一ソース）
                         ▼
┌─ ローカル / Claude Code（手動・オンデマンド） ──────────────┐
│ 1. pull : 当日(+前日)の levels JSON と チャートPNG を R2 からDL  │
│ 2. skill: 「gex-article」で記事生成（GEX_CLAUDE_GUIDELINE準拠）  │
│ 3. 保存 : Obsidian fleet note/ に .md ＋ attachments へチャートコピー │
│ 4. 反復 : ユーザー確認 → 改善要望 → スキル/記事を修正           │
│ 5. 投稿 : Obsidian確定ノートを読んで note へ下書き投稿           │
└──────────────────────────────────────────────┘
```

## R2レイアウト

- levels JSON: `gex/daily/{date}/{symbol}.json`（既存）
- GEX計算結果: `gex/daily/{date}/{symbol}_gex.pkl.gz`（既存）
- チャートPNG: `charts/{date}/{date}_{symbol}_gex.png`（**新規**・日付入りファイル名）

## Obsidian出力レイアウト

```
Obsidian Vault R2/
  fleet note/
    GEX_{date}.md                                  ← 記事本体（フロントマターに title/tags/date/status）
    attachments/{date}/{date}_{symbol}_gex.png     ← R2からコピーした当日チャート
```

- 記事内画像は標準Markdown `![SPY](attachments/{date}/{date}_SPY_gex.png)`（Obsidianも投稿スクリプトも解釈可能）。

## 実装順序

| 順 | 作業 | 場所 |
|---|---|---|
| 1 | チャートPNG → R2アップロード追加（**検証中は git コミットも並行継続**） | CI |
| 2 | R2 pull（最新日付自動／日付引数可。当日＋前日の levels JSON・チャートPNG取得） | ローカル |
| 3 | スキル `Gamma_SnR/.claude/skills/gex-article/`（build_promptの指示を移植、git管理） | skill |
| 4 | Obsidian出力（`fleet note/GEX_{date}.md` ＋ `attachments/{date}/` へチャートコピー） | ローカル |
| 5 | `scripts/post_note_from_obsidian.mjs` を新規作成（`8_post_note.mjs` のコピーを Obsidian対応に改修） | 投稿 |
| 6（最後） | ローカル完結フロー動作確認後に：**git チャートコミット停止** ＋ **workflowから step7/step8 呼び出し除去**（スクリプト本体は残す） | CI |

## 確定した決定事項

1. **チャートgitコミット**：移行検証中は git＋R2 を並行。完全移行確認後（手順6）に git コミット停止。過去の `charts/` は残置。
2. **スキル置き場**：プロジェクト内 `.claude/skills/`、git管理。
3. **pull対象日付**：R2の最新日付を自動選択。引数で日付指定も可。
4. **既存スクリプトの温存**：
   - `7_generate_note_article.py`：削除しない（手順6でCIから呼び出しを外すのみ）。
   - `8_post_note.mjs`：そのまま温存。Obsidian対応は**コピー**（`post_note_from_obsidian.mjs`）で実装。

## OI急増銘柄の導出（補足）

`symbols_oi_surge.json` を R2 に置く必要はない。`gex/daily/{date}/` に存在する銘柄から「指数・M7・ETFを除いた残り」を `|totalGEX|` 降順で上位5件、という現行ロジックをローカルで再現する。

## 銘柄グループ（現行の記事構成）

- 指数: SPY, QQQ, IWM, SMH
- M7: AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA
- OI急増: 上記以外の上位（簡易紹介）
