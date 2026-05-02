# Gamma_SnR - GEX Data Pipeline

オプションチェーンデータからGEX（ガンマエクスポージャー）レベルを計算し、Cloudflare R2 に保存するパイプライン。

---

## 📋 概要

### GEX レベルとは
マーケットメーカーのガンマヘッジ活動により形成されるサポート/レジスタンスレベル。

| レベル | 説明 |
|--------|------|
| **HVL (Gamma Flip)** | Net GEXが正⇔負に切り替わる価格。上なら安定・下なら不安定 |
| **Call Wall** | 最大正Net GEXのストライク（上値抵抗帯） |
| **Put Wall** | 最大負Net GEXのストライク（下値支持帯） |
| **Sentiment** | スポット vs HVL で市場環境を判定 |

### パイプラインフロー
```
[yfinance] → オプションチェーン取得
     ↓
[Black-Scholes] → ガンマ計算 → ストライク別Net GEX
     ↓
[レベル抽出] → HVL, Call/Put Wall, 0DTE
     ↓
[JSON出力] → R2アップロード
```

---

## 🚀 セットアップ

### 1. 環境変数
```bash
cp .env.example .env
# .env を編集（stock-data-pipeline と同じ R2 認証情報を使用）
```

### 2. 依存関係
```bash
pip install -r requirements.txt
```

---

## 💻 使用方法

### ローカル実行
```bash
python scripts/1_fetch_options_data.py
python scripts/2_calculate_gex.py
python scripts/3_extract_levels.py
python scripts/4_export_to_json.py
python scripts/5_upload_to_r2.py

# クリーンアップ
rm -rf data/*
```

### 銘柄指定で実行
```bash
python scripts/1_fetch_options_data.py --symbols SPY AAPL TSLA
```

### GitHub Actions
- **自動**: 月〜金 22:30 UTC（stock-data-pipeline の後）
- **手動**: Actions タブから "Daily GEX Update" → "Run workflow"

---

## 📊 R2 データ構造

```
r2://stock-data/
└── gex/
    └── daily/
        ├── 2026-02-24/
        │   ├── SPY.json
        │   ├── QQQ.json
        │   └── ...
        └── latest.json     # 最新メタ情報
```

### JSON スキーマ例
```json
{
  "ticker": "SPY",
  "date": "2026-02-24",
  "spotPrice": 502.30,
  "totalGEX": 1250000000,
  "sentiment": "positive_gamma",
  "levels": {
    "hvl": 498.5,
    "callWall": 510.0,
    "putWall": 490.0,
    "callWalls": [
      {"strike": 510.0, "netGEX": 500000000}
    ],
    "putWalls": [
      {"strike": 490.0, "netGEX": -400000000}
    ]
  },
  "profile": [...],
  "zeroDTE": {...},
  "metadata": {...}
}
```

---

## 🎯 Phase 1 対象銘柄

| 銘柄 | 説明 |
|------|------|
| SPY | S&P 500 ETF |
| QQQ | NASDAQ 100 ETF |
| SOXX | 半導体 ETF |
| DIA | ダウ平均 ETF |
| IWM | ラッセル2000 ETF |

---

## 📄 ライセンス

Private repository - All rights reserved.
