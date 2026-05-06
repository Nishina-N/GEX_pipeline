# GEX プロファイル解説ガイドライン（Claude向け）

このドキュメントは、GEXプロファイルを解説するClaudeが正確な理解を持つためのガイドラインです。  
コードの実装（`2_calculate_gex.py` / `3_extract_levels.py` / `visualize_gex.py`）に基づく事実のみを記載しています。

---

## 1. GEXとは

**GEX（Gamma Exposure）** は、マーケットメーカー（MM）のガンマヘッジ圧力を金額換算した指標です。

### 計算式（`2_calculate_gex.py` より）

```
GEX_call(K) = +OI_call(K) × Γ_BAW × Multiplier × S² × 0.01
GEX_put(K)  = -OI_put(K)  × Γ_BAW × Multiplier × S² × 0.01
NETGEX(K)   = GEX_call(K) + GEX_put(K)
```

- **コール: プラス符号** → MMは原資産を買ってヘッジ（上値抑制）
- **プット: マイナス符号** → MMは原資産を売ってヘッジ（下値支持）
- **Multiplier = 100（contract_size）、× 0.01 = 実質 OI × Γ × S²**

### モデル: Barone-Adesi Whaley (BAW)

個別株オプションはアメリカン・オプション（早期行使可能）のため、Black-Scholesではなく**BAWモデル**を使用。数値二次微分でガンマを算出：

```
Γ_BAW(S) = (V(S+ΔS) - 2V(S) + V(S-ΔS)) / ΔS²   （ΔS = S × 0.001）
```

---

## 2. ⚠️ 最重要誤解：totalGEXがマイナスでもCall Wallは存在する

### 誤解

> 「totalGEXがマイナスだから、Positive γ（上値抵抗）はない」

### 正しい理解

```
totalGEX = Σ NETGEX(K)   ← 全ストライクの合計（スカラー値）
```

**totalGEXは全ストライクの総和にすぎない。個別ストライクのNETGEXの正負とは独立している。**

#### 数値例

| ストライク | NETGEX |
|-----------|--------|
| 150       | +800M  |  ← Call Wall（正のNETGEX）
| 145       | +200M  |
| 140       | -600M  |
| 135       | -1000M |  ← Put Wall（負のNETGEXが最大絶対値）
| 130       | -800M  |
| **合計**  | **-1400M** ← totalGEXはマイナス |

→ **totalGEX = -1400M でも Call Wall（150）は明確に存在する**

### sentimentの決定ロジック（`3_extract_levels.py` より）

```python
sentiment = 'positive_gamma' if spot_price > hvl else 'negative_gamma'
```

**sentimentは `totalGEX` の符号ではなく、Spot価格とHVLの位置関係で決まる。**

- `spot > HVL` → `positive_gamma`（Positive GEX環境）
- `spot < HVL` → `negative_gamma`（Negative GEX環境）

---

## 3. チャート構造の読み方

チャートは**3パネル構成**です。

### 左パネル：ローソク足 + GEXレベル水平線

- **ローソク足**：過去100日分の株価推移
- **未来領域**（ローソク足右側）：GEXレベル線（約27営業日分）
  - 🟢 緑線：Call Wall（上値抵抗）- 太い実線が最強のCall Resistance
  - 🔴 赤線：Put Wall（下値支持）- 太い実線が最強のPut Support
  - ⚫ 黒破線：HVL（Gamma Flip Level）
  - 🟡 黄線：Spot価格（現在価格）
  - 🟡 薄黄帯：Transition Zone（Put Wall〜Call Wallの帯域）

### 中央パネル：短期GEXヒストグラム（Short-term, DTE 0-7）

- **DTE 0〜7日の満期オプション**のみのGEXを集計
- **足元のヘッジ圧力**を示す（週次SQ等）
- 横棒グラフ：右向き（正）= Call GEX圧力、左向き（負）= Put GEX圧力
- パネル内に短期固有のHVL・Call Wall・Put Wall線を表示

### 右パネル：長期GEXヒストグラム（Long-term, Monthly SQ）

- **次の2つの月次SQ（第3金曜）**の満期オプションのみを集計
- **2週間スイングの本命となる岩盤**を示す
- 構造は中央パネルと同様
- 中央パネルと右パネルのHVLを折れ線で接続（短期→長期のGamma Flip推移）

### 右下情報パネル

| 項目 | 内容 |
|------|------|
| Data | データ取得日 |
| GEX | totalGEX値（BまたはM単位）+ sentiment（Positive/Negative GEX） |
| HVL | 全満期合算のGamma Flip Level |
| Call / Put | Call Wall / Put Wall の価格 |

---

## 4. GEXレベルの定義と解釈

### HVL（High Volatility Level / Gamma Flip）

```
NETGEX(K_i) > 0 かつ NETGEX(K_{i+1}) < 0 の点を線形補間で算出
HVL = K_i + (K_{i+1} - K_i) × |NETGEX(K_i)| / (|NETGEX(K_i)| + |NETGEX(K_{i+1})|)
```

**解釈**：
- `Spot > HVL`：**Positive GEX環境**。MMのヘッジが価格安定化方向に働く。低ボラ・レンジ相場になりやすい。
- `Spot < HVL`：**Negative GEX環境**。MMのヘッジが価格方向に追随する。高ボラ・トレンド相場になりやすい。

### Call Wall（Call Resistance）

```python
# 3_extract_levels.py
positive = df[df['netGEX'] > 0].nlargest(top_n, 'netGEX')
```

- **NETGEX > 0 のストライクを降順に並べた上位1〜3本**
- 上位1本（Call Resistance）：MMがショートヘッジを最も積み増す価格帯 → **最強の上値抵抗**
- 価格がここに近づくとMMが売りを増やし上値が重くなる

### Put Wall（Put Support）

```python
# 3_extract_levels.py
negative = df[df['netGEX'] < 0].nsmallest(top_n, 'netGEX')
```

- **NETGEX < 0 のストライクを絶対値降順に並べた上位1〜3本**
- 上位1本（Put Support）：MMがロングヘッジを最も積み増す価格帯 → **最強の下値支持**
- 価格がここに近づくとMMが買いを増やし下値が支えられる（一時的）

### Transition Zone（遷移帯）

```python
# 3_extract_levels.py
transition_zone = {'lower': put_wall, 'upper': call_wall}
```

- **Put Wall〜Call Wallの間の帯域**
- NETGEXの正負が混在し、価格が明確な方向性を持ちにくい**中立ゾーン**
- チャートでは薄黄色の帯として表示

---

## 5. よくある誤解と正しい解釈

| # | 誤解 | 正しい解釈 |
|---|------|-----------|
| 1 | totalGEXがマイナス → Call Wallは存在しない | totalGEXは全ストライクの合計。個別ストライクには正のNETGEX（Call Wall）が存在しうる |
| 2 | totalGEXがマイナス → Negative GEX環境 | sentimentはSpot vs HVLの位置関係で決まる（totalGEXの符号ではない） |
| 3 | GEXが大きい＝OIが多い | GEXはOI × γ × S²。ATMに近いほどγが大きく、同じOIでもGEXは変わる |
| 4 | Call WallはSpotより必ず上にある | Call WallはNETGEX上位のストライク。SpotがCall Wallを超えている状態もある |
| 5 | HVLは常にSpotとCall/Put Wallの間にある | HVLはNETGEXのゼロクロス点であり、Spot・Wallとは独立した値 |
| 6 | 短期と長期のWallは一致する | 別々に計算。短期（DTE 0-7）と長期（月次SQ）では別のWallが存在する |
| 7 | Positive GEX環境では価格が上昇する | Positive GEXはボラ抑制（レンジ）を示すだけ。方向性はSpotのWall相対位置で判断 |

---

## 6. 価格方向性・レンジ幅の分析フレームワーク

以下の5ステップで、現在価格がCall Wall方向に動きやすいか、Put Wall方向に動きやすいかを判断します。

### STEP 1：sentimentの確認（Spot vs HVL）

```
Spot > HVL → Positive GEX環境（低ボラ・レンジ傾向）
Spot < HVL → Negative GEX環境（高ボラ・トレンド傾向）
```

| 環境 | 特性 | 方向バイアス |
|------|------|-------------|
| Positive GEX | MMヘッジが価格安定化。ボラ低下傾向 | Transition Zone内で往復。Wallに抑えられやすい |
| Negative GEX | MMヘッジが価格追随。ボラ拡大傾向 | トレンドが発生しやすい。Wallを超えると加速しやすい |

### STEP 2：Transition Zone内でのSpot位置

```
Spot の位置 = (Spot - Put Wall) / (Call Wall - Put Wall) × 100%
```

| Spotの位置 | 解釈 |
|-----------|------|
| 70%以上（Call Wall寄り） | Call Wall付近の抵抗が強い。上値が重く、反落しやすい |
| 30〜70%（中間） | 中立。どちらのWallへも等距離 |
| 30%以下（Put Wall寄り） | Put Wall付近の支持が強い。下値が堅く、反発しやすい |

### STEP 3：totalGEXの絶対値とWall強度でレンジ幅を判断

- **|totalGEX|が大きい** → 全体的なGEXが強く、Wallが機能しやすい。レンジが維持されやすい
- **|totalGEX|が小さい（ゼロ付近）** → GEX全体が弱く、Wallが機能しにくい。価格が抜けやすい

**Wall強度（NETGEX値）の確認**：  
Call WallのNETGEX値 vs Put WallのNETGEXの絶対値を比較。

| 比較結果 | 解釈 |
|---------|------|
| Call WallのNETGEX ≫ \|Put Wall のNETGEX\| | 上値抵抗が強い。Put Wall方向（下方）に動きやすい |
| \|Put WallのNETGEX\| ≫ Call WallのNETGEX | 下値支持が強い。Call Wall方向（上方）に動きやすい |
| ほぼ均等 | どちらにも等しい圧力。方向感なし |

### STEP 4：方向性バイアスの総合判断

STEP 1〜3を統合して判断します。

| 条件の組み合わせ | 方向性バイアス |
|----------------|--------------|
| Positive GEX + Spot が Put Wall 寄り + \|Put GEX\| 強い | **Call Wall方向（上方）へ動きやすい**。ただし上値は限定的（レンジ内） |
| Positive GEX + Spot が Call Wall 寄り + Call GEX 強い | **Put Wall方向（下方）へ動きやすい**。ただし下値も限定的（レンジ内） |
| Negative GEX + Spot が HVL 下 | **トレンド継続リスク**。直近のWallまで引き寄せられやすい |
| Positive GEX + Spot が Transition Zone 中間 | **レンジ継続**。明確な方向感なし |

### STEP 5：短期 vs 長期のWall位置比較

```
短期（DTE 0-7）：週次SQまでの足元のヘッジ圧力
長期（月次SQ）  ：2週間スイングの本命となる岩盤
```

| 比較パターン | 解釈 |
|------------|------|
| 短期Call Wall < 長期Call Wall | 短期では手前に壁がある。超えれば長期Wallまで伸びやすい |
| 短期Call Wall ≈ 長期Call Wall | 同ストライクに壁が重なる。非常に強い上値抵抗 |
| 短期Put Wall > 長期Put Wall | 短期では手前に支持がある。割れれば長期Wallまで落ちやすい |

---

## 7. ⛔ Wall突破後の急騰・急落リスク

### なぜWall突破後に価格が加速するのか

Wall突破後に価格が急激に動く理由は、以下の3つのメカニズムによります：

**① MMのヘッジ方向の反転**  
Call Wallを上抜けすると、そのストライクのNETGEXが消滅または逆転し、MMの売りヘッジが買いヘッジに転換。価格上昇が自己強化される。

**② 次のWallまでの「空白地帯」**  
Transition Zoneを抜けた後、次のCall Wall / Put Wallまでの区間はNETGEXが小さく、価格を止める力が弱い。価格は次のWallまで比較的スムーズに動く。

**③ Negative GEXへの転落**  
HVLを突破すると環境がPositive GEX（低ボラ）からNegative GEX（高ボラ）に変わり、MMのヘッジが価格方向に追随する。ボラティリティの自己強化が始まる。

### Call Wall突破シナリオ

```
Spot が Call Wall（上値抵抗）を終値ベースで上抜け・維持
↓
上方の次のCall Wall or 長期Call Wallまでの「空白地帯」を急上昇
↓
（HVLも超えていれば）Positive GEX環境が継続し、ボラ低下で上値が軽くなる
```

### Put Wall突破シナリオ

```
Spot が Put Wall（下値支持）を終値ベースで下抜け・維持
↓
下方の次のPut Wall or 長期Put Wallまでの「空白地帯」を急落
↓
（HVLも割れていれば）Negative GEX環境に転落し、MMのヘッジが下落を加速させる
```

### 本物のブレイク vs ダマシの判断基準

| 判断軸 | 本物のブレイク | ダマシ（フェイクアウト） |
|--------|-------------|-------------------|
| 出来高 | Wall突破時に急増 | 低調なまま |
| 終値 | Wallの外側で確定 | Wallの内側に戻る |
| 翌日継続 | 翌日もWallの外側を維持 | 翌日にWall内へ戻る |
| GEXの変化 | WallのNETGEX値が低下・消滅 | NETGEXが高いまま維持 |

> **⚠️ 重要**：イントラデイで一時的にWallを抜けても、終値がWall内に戻る場合はダマシと判断する。終値ベースでの確認が原則。

---

## 8. 解説例文テンプレート

### Positive GEX環境（Spot > HVL）の場合

```
[銘柄]は現在、Positive GEX環境にあります（Spot: {spot}, HVL: {hvl}）。
totalGEXは{total_gex}で、マーケットメーカーのヘッジが価格安定化方向に
働いているため、低ボラティリティ・レンジ相場が継続しやすい状況です。

上値の壁はCall Wall（{call_wall}）で、NETGEXは{call_gex}。
下値の支持はPut Wall（{put_wall}）で、NETGEXの絶対値は{put_gex}。
Transition Zoneは{tz_lower}〜{tz_upper}の帯域です。

[方向性バイアスの記述：STEP 2-4の判断結果]

短期（DTE 0-7）では{st_call_wall}が上値、{st_put_wall}が下値の目安。
長期（月次SQ）では{lt_call_wall}が上値、{lt_put_wall}が下値の岩盤です。
```

### Negative GEX環境（Spot < HVL）の場合

```
[銘柄]は現在、Negative GEX環境にあります（Spot: {spot}, HVL: {hvl}）。
totalGEXは{total_gex}で、マーケットメーカーのヘッジが価格方向に追随するため、
高ボラティリティ・トレンド相場が発生しやすい状況です。

直近の上値抵抗はCall Wall（{call_wall}）、下値支持はPut Wall（{put_wall}）。

[方向性バイアスの記述：STEP 2-4の判断結果]

⚠️ HVL（{hvl}）を回復できるかが重要な分岐点です。
HVL回復 → Positive GEX環境に転換し、ボラ低下・安定化へ。
HVL維持（Spot < HVL継続）→ トレンド継続・ボラ拡大のリスク。

Call Wall（{call_wall}）を上抜ければ次の壁（{next_call_wall}）まで、
Put Wall（{put_wall}）を下抜ければ次の壁（{next_put_wall}）まで、
比較的スムーズに動く可能性があります。
```

---

## 付録：JSONデータ構造と各フィールドの対応

```json
{
  "ticker": "AAPL",
  "spotPrice": 195.50,
  "totalGEX": -1400000000,       // ← マイナスでもCall Wallは存在する
  "sentiment": "positive_gamma", // ← totalGEXの符号ではなくSpot vs HVLで決まる
  "levels": {
    "hvl": 192.0,                 // Gamma Flip Level
    "callWall": 200.0,            // 最強の上値抵抗（NETGEX最大のストライク）
    "putWall": 185.0,             // 最強の下値支持（NETGEX絶対値最大のストライク）
    "callWalls": [                // 上位3本のCall Wall
      {"strike": 200.0, "netGEX": 800000000},
      {"strike": 197.5, "netGEX": 200000000}
    ],
    "putWalls": [                 // 上位3本のPut Wall（NETGEX < 0）
      {"strike": 185.0, "netGEX": -1000000000},
      {"strike": 190.0, "netGEX": -600000000}
    ],
    "transition_zone": {"lower": 185.0, "upper": 200.0},
    "short_term": { ... },        // 短期（DTE 0-7）のWall・HVL
    "long_term":  { ... }         // 長期（月次SQ）のWall・HVL
  }
}
```

> `totalGEX = -1.4B（マイナス）` でも `callWalls[0].netGEX = +800M（プラス）` が存在する。  
> これが「totalGEXがマイナスでもCall Wallは存在する」ことを構造で示しています。
