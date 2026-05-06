# GEX プロファイル解説ガイドライン（Claude向け）

このドキュメントは、GEXプロファイルチャートを正確に解説するためにClaudeが参照するガイドライン。
コードベース（`2_calculate_gex.py`, `3_extract_levels.py`, `visualize_gex.py`）の実装に基づいた正確な情報を記載する。

---

## 1. GEXとは何か

GEX（Gamma Exposure / ガンマエクスポージャー）は、マーケットメーカー（MM）がオプションのデルタヘッジ活動を通じて株価に与える圧力を定量化した指標。

### 計算式（`2_calculate_gex.py`より）

```
GEX_call(K) = +OI_call × Γ_BAW × ContractSize × S² × 0.01
GEX_put(K)  = -OI_put  × Γ_BAW × ContractSize × S² × 0.01

Net GEX(K) = GEX_call(K) + GEX_put(K)
```

- `OI_call / OI_put`: そのストライクのコール/プットのOpen Interest
- `Γ_BAW`: BAW（Barone-Adesi Whaley）モデルで算出したガンマ（数値二次微分）
- `ContractSize = 100`, `0.01` → 実質 `OI × Γ × S²`
- コールはプラス（MMは株を**買って**ヘッジ → 上昇抑制になる）
- プットはマイナス（MMは株を**売って**ヘッジ → 下落抑制になる）

### なぜBAWモデルを使うか

個別株オプションはアメリカン・オプション（早期行使可能）。Black-ScholesではディープITMストライクのガンマを**過大評価**するため、実際には弱い壁を強く見せてしまう。BAWモデルは早期行使プレミアムを考慮し、壁の強度を正しく算出する。

---

## 2. ⚠️ 最重要：「totalGEXがマイナス」≠「ポジティブγがない」

### よくある誤解

> 「totalGEX（全ストライク合計）がマイナスだから、ポジティブガンマは存在しない」

**これは誤り。**

### 正しい理解

`totalGEX` は全ストライクの Net GEX を足し合わせた**合計値**に過ぎない。

```
totalGEX = Σ netGEX(K)  [全ストライクK の合計]
```

合計がマイナスであっても、**個別ストライクでは正（プラス）の netGEX が存在しうる**。

#### 具体例

| ストライク | netGEX | 役割 |
|-----------|--------|------|
| 560 | +300M | Call Wall（上値抵抗） |
| 540 | +150M | Call Wall 2（上値抵抗） |
| 510 | -200M | （中立ゾーン） |
| 490 | -600M | Put Wall（下値支持） |
| 470 | -150M | Put Wall 2 |
| **合計** | **-500M** | **totalGEX = マイナス** |

この例では `totalGEX = -500M`（マイナス）だが、560と540には**正のnetGEX（Call Wall）が存在**する。
これらのCall WallsはMMの買いヘッジ圧力として実際に機能する**上値抵抗**として機能する。

### Sentimentの決まり方（コードより）

```python
# 3_extract_levels.py
sentiment = 'positive_gamma' if spot_price > hvl else 'negative_gamma'
```

**`sentiment` は `totalGEX` の符号ではなく、Spot価格とHVL（ガンマフリップレベル）の位置関係で決まる。**

- Spot > HVL → `"positive_gamma"` → チャートに "Positive GEX" と表示
- Spot < HVL → `"negative_gamma"` → チャートに "Negative GEX" と表示

つまり：
- `totalGEX` がマイナスでも、Spot > HVL なら "Positive GEX" 表示になる
- `totalGEX` がプラスでも、Spot < HVL なら "Negative GEX" 表示になる

---

## 3. チャートの構造

GEXチャートは 3 つのパネルで構成される。

```
┌─────────────────────────┬──────────┬──────────┐
│                         │  Short   │  Long    │
│    ローソク足チャート    │  Term    │  Term    │
│    （左パネル・大）     │  GEX     │  GEX     │
│                         │ ヒスト   │ ヒスト   │
├─────────────────────────┤          │          │
│       出来高             │          │          │
└─────────────────────────┴──────────┴──────────┘
```

### 左パネル：ローソク足チャート

- 直近100日のローソク足 + 出来高（下部）
- **未来27営業日の領域**にGEXレベルラインを表示（現在はデータがないため線のみ）
- 価格軸（Y軸）は右2つのヒストグラムと共有

| 色・線種 | 意味 |
|---------|------|
| 琥珀色（Amber）実線 | 現在のSpot価格 |
| 緑（Green）実線 | Call Wall（上値抵抗）最強1本 |
| 緑点線 | Call Wall 2, 3 |
| 朱色（Crimson）実線 | Put Wall（下値支持）最強1本 |
| 朱色点線 | Put Wall 2, 3 |
| 墨色（INK）破線 | HVL（ガンマフリップ） |
| 琥珀色陰影 | Transition Zone（遷移帯） |

HVLラベルには現在の環境が表示される：
- `HVL:xxx.x [+γ]` → Spot > HVL（Positive GEX環境）
- `HVL:xxx.x [-γ]` → Spot < HVL（Negative GEX環境）

### 中央パネル：Short-term GEXヒストグラム

- **集計対象**: 残存日数（DTE）が 0〜7日の全満期
- 足元のヘッジ活動による壁（今週の抵抗/支持）を示す
- HVL、Call Wall、Put Wallがパネル固有のものとして表示される

### 右パネル：Long-term GEXヒストグラム

- **集計対象**: 次の月次SQ（第3金曜）とその次の月次SQの合計2限月
- 2週間スイングの岩盤的な壁を示す

### ヒストグラムの読み方

```
← マイナス │ 0 │ プラス →
         ──┼───┼──────────────
   strike1 │   │█████████   (+) → Call Wall: MMの買いヘッジ圧力
   strike2 │   │████        (+) → Call Wall 2
   strike3 │   │            (≈0)
   strike4 ████│            (-) → Put Wall: MMの売りヘッジ圧力
   strike5 ██  │            (-) → Put Wall 2
```

- **右側（プラス）のバー**: Call GEXがPut GEXを上回る → MMが株を買ってヘッジ → **上値抵抗**として機能
- **左側（マイナス）のバー**: Put GEXがCall GEXを上回る → MMが株を売ってヘッジ → **下値支持**として機能（反転の起点になりうる）
- バーの長さは**パネル内最大絶対値で正規化**（-1〜+1スケール）
  → 短期と長期パネルのバー長さは**直接比較できない**（それぞれ別スケール）

---

## 4. GEXレベルの定義と解釈

### HVL（High Volatility Level / Gamma Flip）

Net GEXの符号が切り替わるゼロクロス点。線形補間で算出。

```
HVL = K_i + (K_{i+1} - K_i) × |netGEX(K_i)| / (|netGEX(K_i)| + |netGEX(K_{i+1})|)
```

| 状況 | 市場環境 |
|------|---------|
| Spot > HVL | Positive GEX：MMヘッジが価格を安定させる → 低ボラ・レンジ傾向 |
| Spot < HVL | Negative GEX：MMヘッジが価格変動を増幅させる → 高ボラ・トレンド傾向 |

### Call Wall（コールウォール）

- 正の netGEX を持つストライクの中で**最大値のストライク**
- MMの買いヘッジ圧力が最大 → **上値抵抗として機能**
- 価格がCall Wallに近づくとMMはショートデルタを積み増し、上昇を抑制
- Call Wallは3本まで表示（最強の1本 + 上位2, 3本）

### Put Wall（プットウォール）

- 負の netGEX を持つストライクの中で**絶対値最大のストライク**
- MMの売りヘッジ圧力が最大 → **下値支持として機能**（一時的なバウンス起点）
- Put Wallは3本まで表示

### Transition Zone（遷移帯）

- Put WallとCall Wallの間の帯域（Amber陰影で表示）
- 正負のGEXが混在する中立ゾーン
- 帯域内は方向性が出にくいため、レンジ戦略・様子見が有効

---

## 5. 情報パネル（右下）の読み方

| 表示項目 | 内容 | 注意点 |
|---------|------|-------|
| Data | データ取得日 | |
| GEX | 全ストライク合算のtotalGEX + Sentiment | Sentimentは`totalGEX`の符号ではなくSpot vs HVLで決まる |
| HVL | 全満期合算のガンマフリップレベル | |
| Call / Put | 全満期合算の最強Call Wall / Put Wallのストライク | |

**重要**: GEX欄に "Positive GEX" と表示されていても `totalGEX` がプラスとは限らない。
また "Negative GEX" と表示されていても、個別ストライクにCall Wallは存在しうる。

---

## 6. 短期・長期の使い分け

| | Short-term | Long-term |
|--|-----------|----------|
| 集計対象 | DTE 0〜7日の全限月 | 次の2回の月次SQ（第3金曜） |
| 示すもの | 今週の足元ヘッジ圧力の壁 | 2週間スイングの岩盤的な壁 |
| 活用場面 | デイ〜数日の売買判断 | スイングの方向性・利確/損切り目安 |
| 壁の強度 | 満期に向けて急変しうる | 比較的安定した岩盤 |

2つのHVLは接続線で結ばれており、短期→長期のガンマ環境の連続性が視覚的に確認できる。

---

## 7. よくある誤解と正しい解釈

| 誤解 | 正しい解釈 |
|------|----------|
| `totalGEX` がマイナス → ポジティブγが存在しない | `totalGEX` がマイナスでも個別ストライクにはCall Wallが存在する場合がある |
| "Negative GEX" 表示 → 全てのストライクでMMが売り圧力 | "Negative GEX" は Spot < HVL を意味するだけ。個別ストライクのGEX方向は別途確認が必要 |
| "Positive GEX" 表示 → `totalGEX` がプラス | `totalGEX` がマイナスでも Spot > HVL なら "Positive GEX" と表示される |
| ヒストグラムの長いバー = 絶対的に強い壁 | 各パネル内の相対的な強さを示すだけ。短期と長期は別スケールで比較不可 |
| Call WallがあればSpotは必ずそこで止まる | Call Wallは抵抗帯の目安。コールロールアップ等でWallが移動し価格が追随することもある |
| Put WallがあればSpotは必ずそこでバウンスする | Put Wallは一時的なバウンス起点。プットのITM化が進むとロールダウンし下落加速の転換点になることもある |

---

## 8. パイプラインのデータフロー

```
1_fetch_options_data.py  → オプションチェーン取得（yfinance）
         ↓
2_calculate_gex.py       → BAWガンマ計算 → ストライク別Net GEX算出
         ↓                 (全満期 / 短期DTE0-7 / 長期月次SQ2本)
3_extract_levels.py      → HVL, Call/Put Wall, Transition Zone抽出
         ↓                 sentiment = 'positive_gamma' if spot > hvl else 'negative_gamma'
4_export_to_json.py      → R2アップロード用JSON整形
         ↓
visualize_gex.py         → チャート生成（ローソク足 + GEXヒストグラム）
```

---

## 9. チャート解説の例文テンプレート

### Positive GEX 環境の例

> SPYは現在 Positive GEX 環境（Spot $502 > HVL $498）にあります。
> MMのヘッジ活動が価格変動を抑制する方向に働くため、低ボラ・レンジ傾向が続きやすい局面です。
>
> 上値抵抗: Call Wall $510（netGEX +500M）, CW2 $515
> 下値支持: Put Wall $490（netGEX -400M）
> 遷移帯（Transition Zone）: $490〜$510
>
> なお totalGEX は -XXM とマイナスですが、これは合計値であり、
> $510 の Call Wall は実際にMMの買いヘッジ圧力として機能しています。

### Negative GEX 環境の例

> SPYは現在 Negative GEX 環境（Spot $492 < HVL $498）にあります。
> MMのヘッジ活動が価格変動を増幅させる方向に働くため、高ボラ・トレンド傾向になりやすい局面です。
>
> 最も近い上値の壁: Short-term HVL $498（現在値との差: +$6）
> 上値抵抗: Call Wall $510, $515
> 下値支持: Put Wall $480（最初のサポート目安）
>
> Negative GEX 環境でも $510 や $515 のCall Wallは存在します。
> ただし価格がHVLを下回っている間は、これらのWallへの接近よりも
> Put Wallへの下落リスクに注意が必要です。
