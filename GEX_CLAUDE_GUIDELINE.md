# GEX プロファイル解説ガイドライン（Claude向け統合版）

> このドキュメントは、GEXプロファイルを正確に解説・分析するためのClaudeの内部参照ガイドラインです。
> コードベース（`2_calculate_gex.py`, `3_extract_levels.py`, `visualize_gex.py`, `option_dashboard_spec_v2.md`）に基づき、
> 計算方法・グラフの読み方・分析フレームワーク・よくある誤解を網羅しています。

---

## 目次

1. [GEXとは](#1-gexとは)
2. [⚠️ 最重要：totalGEXがマイナスでもCall Wallは存在する](#2-️-最重要totalgexがマイナスでもcall-wallは存在する)
3. [チャート構造の読み方](#3-チャート構造の読み方)
4. [GEXレベルの定義と解釈](#4-gexレベルの定義と解釈)
5. [よくある誤解と正しい解釈](#5-よくある誤解と正しい解釈)
6. [価格方向性・レンジ幅の分析フレームワーク](#6-価格方向性レンジ幅の分析フレームワーク)
7. [⛔ Wall突破後の急騰・急落リスク](#7-️-wall突破後の急騰急落リスク)
8. [解説例文テンプレート](#8-解説例文テンプレート)

---

## 1. GEXとは

### 1.1 計算モデル

このシステムは **Barone-Adesi Whaley (BAW) 近似モデル** を採用している。
個別株オプションはアメリカン・オプション（早期行使可能）のため、ヨーロピアン前提のBlack-Scholesでは
ディープITMのガンマを誤評価する可能性がある。BAWモデルはこれを補正する。

### 1.2 GEX計算式

```python
# 2_calculate_gex.py より
GEX_call(K) = +OI_call(K) × Γ_BAW × contract_size × S² × 0.01
GEX_put(K)  = -OI_put(K)  × Γ_BAW × contract_size × S² × 0.01
# contract_size(=100) × 0.01 = 1 なので実質 OI × Γ × S²

netGEX(K)   = GEX_call(K) + GEX_put(K)  # 各ストライクの正味GEX
totalGEX    = Σ netGEX(K)               # 全ストライク合計
```

### 1.3 符号の意味

| 符号 | 意味 | マーケットメーカー（MM）の行動 |
|------|------|-------------------------------|
| **コール GEX がプラス** | MMはコールを売っている（ショートポジション） | 価格上昇時に原資産を**買い戻し**（上昇圧力を緩和） |
| **プット GEX がマイナス** | MMはプットを売っている（ショートポジション） | 価格下落時に原資産を**売り増し**（下落圧力を増幅） |
| **netGEX(K) > 0** | Call GEX がプット GEX を上回る → **上値抵抗（Call Wall）** |
| **netGEX(K) < 0** | Put GEX がコール GEX を上回る → **下値支持（Put Wall）** |

### 1.4 GEXの時間軸（集計期間）

コードは3つの時間軸でGEXを集計する：

| 集計タイプ | 対象満期 | 意味 |
|-----------|---------|------|
| **全体 (total)** | 全満期を合算 | 市場全体のGEX分布。totalGEXの計算に使用 |
| **短期 (short_term)** | DTE 0〜7日 | 足元のヘッジの壁（週次SQ前後に大きく変動） |
| **長期 (long_term)** | 次の月次SQ × 2本 | 2週間スイングの岩盤（月次SQ=第3金曜） |

---

## 2. ⚠️ 最重要：totalGEXがマイナスでもCall Wallは存在する

### 2.1 誤解のパターン

> ❌ 「totalGEXがマイナスだから、市場はNegative GEX環境で、Call Wallはない」

**これは誤りです。**

### 2.2 正しい理解

```
totalGEX = Σ netGEX(K)  [全ストライクのnetGEXの合計]
```

totalGEXは全ストライクの**代数的な合計**であり、各ストライクの値を相殺した結果です。
合計がマイナスでも、**個別のストライクには正の netGEX（= Call Wall）が存在します。**

### 2.3 数値例

| ストライク | netGEX | 役割 |
|-----------|--------|------|
| 200 | +500M | Call Wall 1（上値抵抗） |
| 195 | +200M | Call Wall 2 |
| 185 | -1,000M | Put Wall 1（下値支持） |
| 180 | -800M | Put Wall 2 |
| **合計** | **-1,100M** | **totalGEX（マイナス）** |

→ **totalGEXは−1,100Mだが、200と195にはCall Wallが存在する。**

### 2.4 "Positive GEX" / "Negative GEX" の判定ロジック

`sentiment` フィールドの判定はコードで以下のように実装されている：

```python
# 3_extract_levels.py
sentiment = 'positive_gamma' if spot_price > hvl else 'negative_gamma'
```

**✅ sentimentはtotalGEXの符号ではなく、Spot価格とHVLの位置関係で決まる。**

| 条件 | sentiment | 表示 | 意味 |
|------|-----------|------|------|
| Spot > HVL | `positive_gamma` | "Positive GEX" | MMが価格安定方向にヘッジ → 低ボラ・レンジ傾向 |
| Spot < HVL | `negative_gamma` | "Negative GEX" | MMが価格方向にヘッジ追随 → 高ボラ・トレンド傾向 |

---

## 3. チャート構造の読み方

### 3.1 全体レイアウト（3パネル構造）

```
┌─────────────────────────────┬──────────┬──────────┐
│                              │ 短期 GEX │ 長期 GEX │
│   左パネル                   │ (DTE0-7) │ (月次SQ) │
│   ローソク足 + GEXレベル線   │ヒストグラ│ヒストグラ│
│                              │    ム    │    ム    │
├──────────────────────────────┤          │          │
│   出来高バー                 │          │          │
└──────────────────────────────┴──────────┴──────────┘
                         右下: 情報パネル
```

### 3.2 左パネル（ローソク足 + GEXレベル線）

- **ローソク足**: 過去100営業日の価格推移
- **未来領域（右側の空白）**: 未来27営業日分の空白スペース。GEXレベル線のラベルを表示するために使用
- **GEXレベル線**: 未来領域に描画される水平線

| 表示 | 色 | 意味 |
|------|-----|------|
| `Call: {price}` | 緑（実線・太） | Call Wall 1（最強の上値抵抗） |
| `CW2: {price}` | 緑（点線・細） | Call Wall 2（二次上値抵抗） |
| `CW3: {price}` | 緑（点線・細） | Call Wall 3（三次上値抵抗） |
| `Put: {price}` | 朱色（実線・太） | Put Wall 1（最強の下値支持） |
| `PW2: {price}` | 朱色（点線・細） | Put Wall 2（二次下値支持） |
| `PW3: {price}` | 朱色（点線・細） | Put Wall 3（三次下値支持） |
| `HVL: {price} [+γ]` | 墨色（破線） | HVL（Spot > HVLならPositive GEX） |
| `HVL: {price} [-γ]` | 墨色（破線） | HVL（Spot < HVLならNegative GEX） |
| `Spot: {price}` | 琥珀色（実線・最太） | 現在価格 |
| 琥珀色の帯 | 琥珀色（半透明） | Transition Zone（Put Wall〜Call Wallの帯） |

### 3.3 短期GEXヒストグラム（中央パネル）

- **対象**: DTE 0〜7日の満期（週次SQ等）
- **横棒**: 各ストライクのnetGEXをパネル内の最大絶対値で正規化（-1〜+1スケール）
- **右向きバー**: netGEX > 0（Call Wall = 上値抵抗）
- **左向きバー**: netGEX < 0（Put Wall = 下値支持）
- **目的**: 足元（今週）の壁の強度分布を確認する

### 3.4 長期GEXヒストグラム（右パネル）

- **対象**: 次の月次SQ × 2本
- **構造**: 短期と同じ横棒ヒストグラム
- **目的**: 2週間スイングの岩盤（中期的な壁）を確認する

### 3.5 HVL結線（中央の折れ線）

- **線**: 短期パネルのHVLと長期パネルのHVLをつなぐ斜線
- **意味**: 足元のGamma Flip水準と中期のGamma Flip水準の関係を視覚化

### 3.6 右下の情報パネル

| 項目 | 内容 |
|------|------|
| Data | データ取得日 |
| GEX | totalGEX（例: "2.5B" または "-800M"）と sentiment |
| HVL | Gamma Flip水準（全体集計） |
| Call / Put | Call Wall 1 / Put Wall 1 の価格 |

---

## 4. GEXレベルの定義と解釈

### 4.1 HVL（High Volatility Level / Gamma Flip）

**定義**: netGEXが正から負（または負から正）に切り替わる価格水準。線形補間で算出。

```
HVL = K_i + (K_{i+1} - K_i) × |NETGEX(K_i)| / (|NETGEX(K_i)| + |NETGEX(K_{i+1})|)
```

**解釈**:
- **Spot > HVL（Positive GEX）**: MMのヘッジが価格安定方向に働く → 低ボラ・レンジ相場になりやすい
- **Spot < HVL（Negative GEX）**: MMのヘッジが価格の動きに追随する → 高ボラ・トレンド相場になりやすい

### 4.2 Call Wall（Call Resistance）

**定義**: netGEX > 0 のストライクのうち、値が最大のもの（上位3本まで表示）

```
Call Wall 1 = argmax_{K: netGEX(K) > 0} netGEX(K)
```

**解釈**: 最強の上値抵抗。価格がここに近づくとMMがショートを積み増し → 上値が重くなる。

### 4.3 Put Wall（Put Support）

**定義**: netGEX < 0 のストライクのうち、絶対値が最大のもの（上位3本まで表示）

```
Put Wall 1 = argmin_{K: netGEX(K) < 0} netGEX(K)
```

**解釈**: 最強の下値支持。価格がここに近づくとMMがロングを積み増し → 一時的なバウンス要因。

### 4.4 Transition Zone（遷移帯）

**定義**: Put Wall 1（下限）〜 Call Wall 1（上限）の価格帯

**解釈**:
- この帯域内では価格が明確な方向性を持ちにくい（MMヘッジが双方向に混在）
- 帯域幅が広いほど、レンジが継続しやすい
- **上抜け確認（終値ベース）** → 短期強気バイアス
- **下抜け確認（終値ベース）** → 短期弱気バイアス

---

## 5. よくある誤解と正しい解釈

| # | ❌ 誤解 | ✅ 正しい解釈 |
|---|---------|-------------|
| 1 | totalGEXがマイナス → Call Wallがない | totalGEXはΣnetGEX(K)の代数和。マイナスでも個別ストライクには正のnetGEX（Call Wall）が存在する |
| 2 | totalGEXがマイナス → Negative GEX環境 | Negative GEX環境の判定はSpot vs HVLの位置関係で決まる（コード参照） |
| 3 | OIが多い = 強いWall | GEXはOI × ガンマ × S²で算出。OTMのストライクはガンマが小さいため、OIが多くてもGEXが小さい場合がある |
| 4 | Put WallがあるとSpotが下がらない | Put Wallはバウンス要因だが、壁を維持できないと下落加速に転じる（投資家のロールダウンが起きると支持が切り下がる） |
| 5 | 短期と長期のWallは同じ意味 | 短期（DTE 0-7）は今週の壁、長期（月次SQ）は中期の岩盤。重要度と動きやすさが異なる |
| 6 | HistogramのバーがCall Wallに見える → 壁 | バーは各パネル内の最大絶対値で正規化されているため、異なるパネル間での大きさの比較はできない |
| 7 | sentimentが "Positive GEX" → 価格が上がる | sentimentは方向性ではなく**ボラティリティ特性**を示す。Positive GEXでも下落はする（ただし安定的に推移しやすい） |

---

## 6. 価格方向性・レンジ幅の分析フレームワーク

現在の価格・Wall・γ・totalGEXから「Call Wall方向に動きやすいか、Put Wall方向に動きやすいか」「上昇・下落の幅は限定的か」を判断するための5ステップフレームワーク。

### STEP 1：sentimentの確認（γ環境の判定）

```
if Spot > HVL → Positive GEX（低ボラ・レンジ傾向）
if Spot < HVL → Negative GEX（高ボラ・トレンド傾向）
```

| 環境 | 特性 | 分析への影響 |
|------|------|------------|
| **Positive GEX** | MMが逆張りヘッジ（価格安定方向） | WallがSpotを挟み込む力が強い。Transition Zone内での往来が続きやすい |
| **Negative GEX** | MMが追随ヘッジ（価格方向に増幅） | 一方向に動き始めると加速しやすい。Wallブレイクのリスク上昇 |

### STEP 2：Transition Zone内のSpot位置確認

```
Transition Zone = [Put Wall 1, Call Wall 1]

Call Wall側距離 = Call Wall 1 - Spot
Put Wall側距離  = Spot - Put Wall 1
```

| SpotのZone内位置 | 解釈 |
|-----------------|------|
| Call Wall 1に近い（上1/3） | Call Wallで抑えられやすい。上抜けブレイクか跳ね返しかの分岐点に接近 |
| 中央付近 | 方向感が出にくい。どちらのWallも同等の影響力 |
| Put Wall 1に近い（下1/3） | Put Wallでバウンスが起きやすい。下抜けブレイクかサポートか分岐 |

### STEP 3：totalGEXの絶対値とWall強度でレンジ幅を判断

**totalGEXの絶対値**は市場全体のGEX力学の強さを示す：

| totalGEX絶対値 | 特性 | レンジ予測 |
|---------------|------|-----------|
| 大（数十億ドル規模） | MMヘッジの影響力が強い | WallがSpotを強く引き付け、レンジが狭くなりやすい |
| 小（数億ドル規模） | MMヘッジの影響力が弱い | Wallの引力が弱く、価格が動きやすい |

**Wall強度（netGEX値）による判断**：

```
Call Wall 1のnetGEX（正値）が大きい → 上値が重い（MM売り圧力が強い）
Put Wall 1のnetGEX（負値の絶対値）が大きい → 下値が堅い（MMロング買い支えが強い）
```

- **両Wall強度が非対称（例：Call Wall >> Put Wall）** → 強い方向への偏りが生じにくい側が強く抵抗、反対側に抜けやすい
- **両Wall強度が均衡** → 方向感なし、レンジ継続

### STEP 4：方向性バイアスの総合判断

上記3つのステップを組み合わせて方向性バイアスを判断する：

**Call Wall方向（上方）に動きやすいケース**：
- Spot が Transition Zone の下半分に位置（Put Wall寄り）かつ
- Put Wall の netGEX絶対値が大きい（強いサポート）かつ
- Negative GEX環境（MMが追随→バウンス後に加速しやすい）

**Put Wall方向（下方）に動きやすいケース**：
- Spot が Transition Zone の上半分に位置（Call Wall寄り）かつ
- Call Wall の netGEX が大きい（強い抵抗）かつ
- Positive GEX環境（抵抗されてレンジ上限から押し返される）

**レンジ継続・方向感なしのケース**：
- Spot が Transition Zone 中央付近かつ
- 両Wall強度が均衡かつ
- Positive GEX環境（MMがSpotを両側から安定させる）

### STEP 5：短期 vs 長期のWall位置比較

```
短期Wall（DTE 0-7）= 今週の壁（週次SQで消滅・変動する）
長期Wall（月次SQ） = 中期の岩盤（2〜4週先まで持続する）
```

| 短期と長期のWallの関係 | 解釈 |
|----------------------|------|
| 短期・長期ともに同じ方向にWallが重なる | 岩盤が厚い。その方向への抵抗または支持が強固 |
| 短期Wallが長期Wallより外側にある | 週次SQ通過後に長期Wallに移行。SQ後に方向性が変わるリスク |
| 短期Wallが存在しない（データなし） | 今週は壁が薄い。価格変動が大きくなりやすい |
| 短期HVLと長期HVLが大きく乖離 | 短期と中期でγ環境が異なる。週をまたぐと市場特性が変わる可能性 |

---

## 7. ⛔ Wall突破後の急騰・急落リスク

> **これは解説で必ず言及すべき重要事項です。**
> WallはSpotを引き付ける力を持ちますが、一度突破されると状況が反転します。

### 7.1 なぜWall突破後に価格が加速するか（3つのメカニズム）

**① MMのヘッジ方向が反転する**

Call Wall突破を例にとると：
- 突破前：MMはCall Wallで原資産を売り（上値抑制）
- 突破後：価格が大きく動きMMの原資産ポジションが逆に大きなリスクとなるため、ヘッジを解消・反転 → 売り圧力が買い圧力に転じ、上昇が加速

**② 次のWallまで「空白地帯」が存在する**

- Call Wall 1を突破した後、Call Wall 2までは強いGEXの壁がない
- この空白地帯では価格を引き付ける力が弱く、次のWallまで一気に移動しやすい
- （Call Wall 2, 3が近くに存在するかをチャートで確認すること）

**③ Negative GEX環境への転落リスク**

- HVL付近でWallブレイクが重なると、SentimentがPositive→Negativeに転換
- Negative GEX環境下ではMMのヘッジが価格方向に追随するため、さらに加速

### 7.2 Call Wall突破後の急騰シナリオ

```
条件: Spot が Call Wall 1 を上抜け（特に終値ベースで維持）

メカニズム:
1. MMのショートヘッジが解消・買い方向に転換 → 追加の買い圧力
2. Call Wall 1 〜 Call Wall 2 の空白地帯を一気に駆け上がる
3. 短期のIVが急上昇し、さらにガンマが拡大するケースも

目標: Call Wall 2、さらに Call Wall 3（存在すれば）
リスク: 出来高を伴わない場合は「ダマシ」の可能性
```

### 7.3 Put Wall突破後の急落シナリオ

```
条件: Spot が Put Wall 1 を下抜け（特に終値ベースで維持）

メカニズム:
1. MMのロングヘッジが解消・売り方向に転換 → 追加の売り圧力
2. Put Wall 1 〜 Put Wall 2 の空白地帯を一気に急落
3. 投資家がプットを下のストライクへロールダウン → Put Wallがさらに切り下がる（下落が止まらない）

目標: Put Wall 2、さらに Put Wall 3（存在すれば）
リスク: ここまで来るとストップロス発動も重なり、急落が加速しやすい
```

### 7.4 本物のブレイク vs ダマシの判断基準

| 判断基準 | 本物のブレイク | ダマシ（フォルスブレイク） |
|---------|--------------|------------------------|
| **出来高** | 平均より明確に多い | 平均以下 / 出来高急落 |
| **終値** | Wallの外側で終値確定 | Wallタッチ後に戻る / 長いヒゲ |
| **翌日以降** | Wall外側を維持・続伸/続落 | 翌日にWall内側に戻る |
| **GEXの変化** | 翌日のGEXでWallが消滅・弱体化 | 翌日もWallが健在 |
| **sentiment** | Negative GEX環境でのブレイク | Positive GEX環境でのブレイク（戻りやすい） |

> **重要な注意点**: Positive GEX環境でのWallタッチは多くの場合「ダマシ」になりやすい。
> Negative GEX環境では、WallをわずかにブレイクするだけでMMのヘッジ反転が起こり、
> 急激な動きに発展するリスクが高い。

---

## 8. 解説例文テンプレート

### 8.1 Positive GEX環境（Spot > HVL）の解説例

```
【GEX概況】Positive GEX環境（Spot {spot:.2f} > HVL {hvl:.2f}）
totalGEX: {total_gex_str}（プラス側・マイナス側の合算値）

【主要レベル（全体）】
Call Wall 1: {call_wall:.1f}（上値抵抗・最強）
Put Wall 1:  {put_wall:.1f}（下値支持・最強）
Transition Zone: {put_wall:.1f} 〜 {call_wall:.1f}

【現在位置の分析】
現在価格{spot:.2f}はTransition Zone内の{position}に位置。
Call Wall方向の距離: {call_dist:.1f}pt、Put Wall方向の距離: {put_dist:.1f}pt。

Positive GEX環境ではMMのヘッジが価格安定方向に働くため、
このTransition Zone内でのレンジ推移が継続しやすい状況。
特に{call_wall:.1f}の上値抵抗（Call Wall、netGEX: {call_netgex_str}）が
強力なため、この水準を終値で上抜けるまでは上値追いは慎重に。

【ブレイクシナリオ】
・Call Wall {call_wall:.1f} 突破（出来高伴う終値確定）→ Call Wall 2: {cw2:.1f} が次の目標
・Put Wall {put_wall:.1f} 下抜け（終値確定）→ Put Wall 2: {pw2:.1f} へ急落リスク、
  さらにHVL割れでNegative GEX転落に注意
```

### 8.2 Negative GEX環境（Spot < HVL）の解説例

```
【GEX概況】Negative GEX環境（Spot {spot:.2f} < HVL {hvl:.2f}）
totalGEX: {total_gex_str}

【主要レベル（全体）】
Call Wall 1: {call_wall:.1f}
Put Wall 1:  {put_wall:.1f}

【現在位置の分析】
現在価格{spot:.2f}はHVL {hvl:.2f}を下回るNegative GEX環境。
MMのヘッジが価格方向に追随するため、一方向に動き始めると加速しやすい。

Put Wall {put_wall:.1f}でのサポートは一時的なバウンス要因となりうるが、
Negative GEX環境では同水準を割り込むと急落が加速するリスクに注意。
HVL {hvl:.2f} の回復（Spot > HVL）を確認するまでは、
反発があっても上値余地は限定的と判断するのが適切。

【ブレイクシナリオ】
・Put Wall {put_wall:.1f} 下抜け → MMのヘッジ解消・反転売りで加速。次のPut Wall 2: {pw2:.1f} を注視
・HVL {hvl:.2f} 回復・Positive GEX転換 → 市場特性が安定方向に転換。Call Wall {call_wall:.1f} が上値目標
```

---

## 付録：コードとデータ構造の対応

```json
// 3_extract_levels.py の出力 JSON 構造（levels フィールド）
{
  "ticker": "AAPL",
  "spotPrice": 200.00,
  "totalGEX": -1100000000,           // 全ストライク合計（マイナスでもCall Wallは存在）
  "sentiment": "negative_gamma",      // Spot < HVL → negative_gamma
  "levels": {
    "hvl": 205.50,                    // Gamma Flip水準
    "callWall": 210.0,                // Call Wall 1
    "putWall": 195.0,                 // Put Wall 1
    "callWalls": [                    // 上位3本
      {"strike": 210.0, "netGEX": 500000000},
      {"strike": 215.0, "netGEX": 200000000},
      {"strike": 220.0, "netGEX": 100000000}
    ],
    "putWalls": [                     // 上位3本（絶対値順）
      {"strike": 195.0, "netGEX": -1000000000},
      {"strike": 190.0, "netGEX": -800000000}
    ],
    "transition_zone": {"lower": 195.0, "upper": 210.0},
    "short_term": { ... },            // 短期集計の同じ構造
    "long_term": { ... }              // 長期集計の同じ構造
  }
}
```

---

*このガイドラインはコードベース（`2_calculate_gex.py`, `3_extract_levels.py`, `visualize_gex.py`, `option_dashboard_spec_v2.md`）に基づいて作成されました。*
*コードの仕様変更時は本ドキュメントも合わせて更新してください。*
