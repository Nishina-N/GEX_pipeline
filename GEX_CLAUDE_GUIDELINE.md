# GEX プロファイル解説 — Claude 向けガイドライン

> **目的**: このドキュメントは、GEXプロファイルを説明するClaudeが正確に解釈・解説できるよう、計算ロジックとグラフの読み方を体系的にまとめたものです。

---

## §1 GEXとは

**GEX（Gamma Exposure）** とは、マーケットメーカー（MM）がオプションのヘッジのために保有する原資産ポジションの感応度を、ドル建てで表した指標です。

### 計算式（コードより: `2_calculate_gex.py`）

```
GEX_call(K) = +OI_call(K) × Γ_BAW × contract_size × S² × 0.01
GEX_put(K)  = -OI_put(K)  × Γ_BAW × contract_size × S² × 0.01
NETGEX(K)   = GEX_call(K) + GEX_put(K)
```

- **コールはプラス**: MMはコール売り → 上昇時に原資産を買い増す（ロングヘッジ）
- **プットはマイナス**: MMはプット売り → 下落時に原資産を売り増す（ショートヘッジ）
- **ガンマモデル**: Barone-Adesi Whaley (BAW) 近似モデルを使用（アメリカン・オプションの早期行使プレミアムを考慮）

### totalGEXとは

```python
total_gex = gex_by_strike['netGEX'].sum()  # 全ストライクのNETGEXの合計
```

全満期・全ストライクにわたるNETGEXの総和です。市場全体のMMヘッジポジションの方向性を示します。

---

## §2 ⚠️ 最重要誤解：totalGEXがマイナスでもCall Wallは存在する

### 誤解
> "totalGEXがマイナスだから、市場はネガティブGEX環境でCall Wallは存在しない"

### 正しい理解

**totalGEXは全ストライクの合計値** であり、個別ストライクの正負とは独立しています。

```
totalGEX = Σ NETGEX(K)  [全ストライクの代数和]
```

具体的な数値例：

| ストライク | NETGEX |
|-----------|--------|
| 500 (Put Wall) | −3,000M |
| 510 | −500M |
| 520 (HVL) | 0 |
| 530 (Call Wall) | **+800M** ← 正のNETGEX（Call Wallとして機能） |
| 540 | +200M |
| **totalGEX** | **−2,500M（マイナス）** |

→ totalGEXが−2,500Mであっても、530には正のNETGEX（Call Wall）が存在します。

### sentimentの判定ロジック（コードより: `3_extract_levels.py`）

```python
sentiment = 'positive_gamma' if spot_price > hvl else 'negative_gamma'
```

**sentimentはtotalGEXの符号ではなく、Spot価格とHVLの位置関係で決まります。**

| Spot vs HVL | sentiment | 表示 |
|-------------|-----------|------|
| Spot > HVL | positive_gamma | "Positive GEX" |
| Spot < HVL | negative_gamma | "Negative GEX" |

---

## §3 チャート構造の読み方

### 3パネル構成（コードより: `visualize_gex.py`）

```
[左パネル: ローソク足 + GEXレベル] [中: 短期GEX] [右: 長期GEX]
```

| パネル | 内容 | 時間軸 |
|--------|------|--------|
| **左パネル（メイン）** | ローソク足（過去100営業日）＋出来高＋GEXレベル水平線 | 全満期合算 |
| **中パネル（短期）** | GEXヒストグラム（横棒グラフ） | DTE 0〜7日 |
| **右パネル（長期）** | GEXヒストグラム（横棒グラフ） | 今日〜対象の月次SQまで累積（短期も内包。対象SQ=次のSQ、ただし次のSQが目前(DTE≤7)の週は次の次のSQ） |

### 左パネルに表示されるGEXレベル線

| 線の種類 | 色 | 意味 |
|---------|-----|------|
| `Call: XXXXX` | 緑（実線・太） | Call Wall（最強の上値抵抗） |
| `CW2: XXXXX` | 緑（点線・細） | Call Wall 2本目 |
| `CW3: XXXXX` | 緑（点線・細） | Call Wall 3本目 |
| `Put: XXXXX` | 朱（実線・太） | Put Wall（最強の下値支持） |
| `PW2: XXXXX` | 朱（点線・細） | Put Wall 2本目 |
| `PW3: XXXXX` | 朱（点線・細） | Put Wall 3本目 |
| `HVL: XXXXX [+γ]` | 墨（破線） | HVL（Gamma Flip Level） |
| `Spot: XXXXX` | 琥珀（実線） | 現在価格 |
| 琥珀の背景帯 | 琥珀（薄い塗り） | Transition Zone |

### GEXヒストグラムの読み方

- **右に伸びるバー（正のNETGEX）** → Call Wall：MMが上昇ヘッジを積んでいる → 上値抵抗
- **左に伸びるバー（負のNETGEX）** → Put Wall：MMが下落ヘッジを積んでいる → 下値支持
- **バーの長さ** → そのストライクの相対的なWall強度（最大値で正規化）
- **バーの色** → 将来的にIV異常度に連動（現在は墨色固定）
- **バー横の数値** → 最大正・最大負ストライクのNETGEX値（例: `+350M`）

---

## §4 GEXレベルの定義と解釈

### HVL（High Volatility Level / Gamma Flip）

**定義**: NETGEXの符号が正→負（または負→正）に切り替わるゼロクロス点（線形補間）

```python
# 3_extract_levels.py より
# 隣接ストライク間のNETGEXの符号変化を検出し、線形補間でゼロクロス点を算出
zero_strike = s1 + (s2 - s1) * (-g1) / (g2 - g1)
```

**解釈**:
- **Spot > HVL（Positive GEX）**: MMヘッジが価格安定方向に働く → 低ボラ・レンジ相場になりやすい
- **Spot < HVL（Negative GEX）**: MMヘッジが価格トレンド方向に追随 → 高ボラ・トレンド相場になりやすい

### Call Wall（Call Resistance）

**定義**: `NETGEX(K) > 0` のストライクの中でNETGEXが最大のもの

```python
# 3_extract_levels.py より
positive = df[df['netGEX'] > 0].nlargest(top_n, 'netGEX')
```

**解釈**: MMのデルタ中立維持のためのショートヘッジ圧力が最大 → **上値の抵抗帯**

### Put Wall（Put Support）

**定義**: `NETGEX(K) < 0` のストライクの中でNETGEXが最小（最も負）のもの

```python
# 3_extract_levels.py より
negative = df[df['netGEX'] < 0].nsmallest(top_n, 'netGEX')
```

**解釈**: MMのデルタ中立維持のためのロングヘッジ圧力が最大 → **下値の支持帯**

### Transition Zone（遷移帯）

**定義**: Put Wall〜Call Wallの間の帯域

```python
# 3_extract_levels.py より
transition_zone = {'lower': put_wall, 'upper': call_wall}
```

**解釈**: NETGEX正負が混在する中立ゾーン。価格が明確な方向性を持ちにくい。チャートでは琥珀色の薄い背景帯として表示。

---

## §5 よくある誤解と正しい解釈

| # | 誤解 | 正しい解釈 |
|---|------|-----------|
| 1 | "totalGEXがマイナス = Positive GEXはない" | totalGEXの合計がマイナスでも、個別ストライクに正のNETGEX（Call Wall）は存在する（§2参照） |
| 2 | "Positive GEX = 上昇環境" | Positive GEXは「ボラティリティが低い・価格が安定しやすい」環境を意味する。上昇・下落の方向性とは別 |
| 3 | "sentimentはtotalGEXの符号で決まる" | `sentiment`はSpot価格とHVLの位置関係で決まる（`spot_price > hvl` → positive_gamma） |
| 4 | "Call WallはPositive GEX環境のみに存在する" | Call WallはNETGEXが正のストライクの最大値。Negative GEX環境（Spot < HVL）でも存在する |
| 5 | "チャートのsentiment表示はtotalGEXの符号" | `visualize_gex.py`の`sent_str`はJSONの`sentiment`フィールドを読む。計算はHVLとSpotの比較 |
| 6 | "短期と長期のGEXは同一" | 短期=DTE 0〜7日の満期合算、長期=今日〜対象の月次SQまで累積（SQ目前の週は次の次のSQへロール）。Wall位置・HVL・強度がそれぞれ異なる |
| 7 | "WallのNETGEX値は絶対値で比較できる" | ヒストグラムは各パネル内の最大絶対値で正規化されるため、短期バーと長期バーの長さは直接比較不可 |

---

## §6 価格方向性・レンジ幅の分析フレームワーク

現在の価格・Wall・γ・totalGEXから、価格がCall Wall方向に動きやすいか、Put Wall方向に動きやすいか、および動きの幅を分析する5ステップフレームワーク。

---

### STEP 1: sentimentの確認（Positive GEX / Negative GEX）

```python
# 3_extract_levels.py より
sentiment = 'positive_gamma' if spot_price > hvl else 'negative_gamma'
```

| sentiment | 意味 |
|-----------|------|
| **Positive GEX**（Spot > HVL） | MMヘッジが価格安定に働く。低ボラ・レンジ相場になりやすい。Call Wall方向への緩やかな上昇バイアスが生じやすい |
| **Negative GEX**（Spot < HVL） | MMヘッジがトレンドを加速させる。高ボラ・トレンド相場になりやすい。価格方向への追随圧力が強い |

---

### STEP 2: Transition Zone内でのSpot位置の確認

**境界の定義**: `(Call Wall + Put Wall) / 2` を中点とし、それより上を「Call Wall 寄り」、下を「Put Wall 寄り」とする。

```
Put Wall ──── [中点 = (CW + PW) / 2] ──── Call Wall
              ↑                        ↑
        Put Wall 寄り           Call Wall 寄り
```

| Spotの位置 | 意味 |
|-----------|------|
| **Call Wall 寄り**（Spot > 中点） | Call Wallに近い。上値抵抗が迫っている |
| **Put Wall 寄り**（Spot < 中点） | Put Wallに近い。下値支持が迫っている |
| **中点付近**（中点±1%程度） | 方向感なし。どちらにも等距離 |

---

### STEP 3: totalGEXの絶対値とWall強度の確認

**totalGEXの絶対値の目安**（参考値）：

| |totalGEX| | Wall強度の目安 | 特徴 |
|------------|----------------|------|
| > 1B（10億） | **強い** | MMヘッジ圧力が大きく、Wallは容易に崩れない。価格はWall間でレンジを形成しやすい |
| 200M〜1B | **中程度** | 一定のWall機能はあるが、大口フローや重要イベントで崩れる可能性がある |
| < 200M（2億） | **弱い** | Wallの抑制機能が限定的。価格が容易にWallを突破するリスクがある |

> ⚠️ これらは目安であり、個別銘柄・市場環境・絶対的なオプションOI規模によって異なります。

また、各Wall（Call Wall・Put Wall）のNETGEX絶対値が大きいほど、そのWallは強固です。

---

### STEP 4: 方向性バイアスの総合判断

**2×2のMECE分類**（Sentiment × Spot位置）：

| ケース | Sentiment | Spotの位置 | 方向性バイアス |
|--------|-----------|-----------|--------------|
| **① Positive GEX + Call Wall 寄り** | Positive GEX | Call Wall に近い（中点より上） | Call Wallが目前。MMのショートヘッジ圧力が増大 → **上値抵抗が強く、Call Wall方向への上昇は限定的**。反落・Put Wall方向への回帰も考慮すべきレンジ継続。 |
| **② Positive GEX + Put Wall 寄り** | Positive GEX | Put Wall に近い（中点より下） | HVL上だがCall Wallには遠い。MMのデルタヘッジが価格を安定させる典型ケース → **緩やかなCall Wall方向への上昇バイアス**。急騰は起きにくく、レンジ継続戦略が有利。 |
| **③ Negative GEX + Call Wall 寄り** | Negative GEX | Call Wall に近い（中点より上） | HVL下でSpotがCall Wall近傍。上値でMMのショートヘッジと、トレンド加速の二重圧力 → **Call Wall抵抗は強いが、HVL回復（Spotが上昇してHVLを超える）できればPositive GEX転換も**。上抜け or 反落の分岐点。 |
| **④ Negative GEX + Put Wall 寄り** | Negative GEX | Put Wall に近い（中点より下） | HVL下でPut Wallに近い。MMヘッジが下落トレンドを加速させる環境 → **Put Wall方向へ動きやすく、Put Wall突破リスクが高い**。Put Wall突破後は急落加速の可能性（§7参照）。 |

---

### STEP 5: 短期 vs 長期のWall位置比較

**短期（DTE 0〜7日）** は足元の圧力、**長期（今日〜対象の月次SQまで累積／SQ目前の週は次の次のSQ）** は中期的な岩盤を示します。

| 一致/不一致 | パターン | 解釈 |
|-----------|---------|------|
| **一致** | 短期CW ≈ 長期CW | 複数の時間軸でWallが重なる → **極めて強固な抵抗/支持**。ブレイクに大きな力が必要 |
| **不一致①** | 短期CW < 長期CW | 足元の天井が低い。短期の反発余地が限定的 → **近い将来の上値が抑えられる**。短期満期後に状況が変化する可能性 |
| **不一致②** | 短期CW > 長期CW | 短期的に高い天井だが、中期的には低い岩盤 → **短期では動きやすいが、月次SQ前後で天井が切り下がる**リスク |
| **短期データなし** | DTE 0〜7の満期なし | 短期パネルが空 → 短期のヘッジ圧力が存在しない。長期Wallのみを参照 |
| **一致（PW）** | 短期PW ≈ 長期PW | **極めて強固な下値支持**。バウンスが起きやすい |
| **不一致（PW）** | 短期PW > 長期PW | 足元の下値支持が中期より高い。短期満期後に支持が下方シフトするリスク |

---

## §7 ⛔ Wall突破後の急騰・急落リスク

Wall（Call Wall・Put Wall）を価格が突破すると、通常の状態に比べて**急激な価格変動が起きやすい**です。

### なぜ突破後に加速するか（3つのメカニズム）

**① MMのデルタヘッジの方向転換**

突破前（例: Call Wall接近）: MMはコール売りのショートポジションを増やして価格を抑制
突破後: ポジションの急速な巻き戻しが起き、価格上昇を加速

**② 次のWallまでの「空白地帯」**

Call Wall突破後、次のCall Wall 2（CW2）やCW3までの区間はNETGEXが小さい → Wallによる抑制がなく価格が自由に動ける

**③ sentimentの転換（GEX環境の変化）**

Put Wall突破 → Spot < HVL → Negative GEXに転落 → MMヘッジがトレンドを加速させる方向に転換 → さらなる下落加速

---

### Call Wall突破シナリオ（上方ブレイク）

```
現在: Spot < Call Wall（Transition Zone上限付近）
       ↓ Call Wall突破
突破後: MMのショートヘッジ巻き戻し + 次のCW2まで空白地帯
       → 急騰加速の可能性
```

| 確認事項 | 内容 |
|---------|------|
| CW2までの距離 | 近い → 比較的短期間でCW2に達し減速。遠い → 急騰が長引く可能性 |
| 長期Call Wall位置 | 長期CWがあればそこで止まる可能性。なければさらに伸びる |
| sentimentの変化 | Positive GEX維持 → 上昇が安定。HVL > Call Wallの場合はNegative GEX転換なし |

---

### Put Wall突破シナリオ（下方ブレイク）

```
現在: Spot > Put Wall（Transition Zone下限付近）
       ↓ Put Wall突破
突破後: MMのロングヘッジ巻き戻し + Negative GEX転落 + 次のPW2まで空白地帯
       → 急落加速の可能性
```

| 確認事項 | 内容 |
|---------|------|
| PW2・PW3の位置 | 次の下値支持。近ければ急落は短期間で一服 |
| HVLとの位置関係 | Spot < HVL（Negative GEX転落）になる場合 → 下落加速リスクが最大 |
| 長期Put Wall | 長期PW近傍ならバウンスの可能性。遠ければ急落継続リスク |

---

### 本物のブレイク vs ダマシの判断基準

| 判断基準 | 本物のブレイク | ダマシ（フォールスブレイクアウト） |
|---------|-------------|--------------------------|
| 出来高 | 急増（通常の1.5倍以上） | 通常〜低め |
| 終値 | Wall価格を明確に超えて終値 | Wall付近で引けたまま、または戻る |
| 翌日の動き | 継続してWall外で推移 | 翌日にWall内に引き戻す |
| GEXの変化 | 翌日のGEX更新でWallが消滅・移動 | WallのNETGEXが維持される |

> ⚠️ **単日のWall価格タッチはブレイクではありません**。終値ベースでのWall超えと、翌営業日の維持を確認してから「ブレイク確定」と判断してください。

---

### 短期Wallのみ突破（長期Wallは維持）のケース

短期Wallのみ突破し、長期Wallが残存している場合：

- **上方ブレイク（短期CW突破）**: 足元のヘッジ圧力は消滅するが、長期CWが次の天井として機能 → 長期CWまでの上昇は想定内、長期CW突破は別途確認
- **下方ブレイク（短期PW突破）**: 足元の支持は消滅するが、長期PWが下値の岩盤として機能 → 長期PWまでの下落は想定内

---

## §8 解説例文テンプレート

### Positive GEX環境の例文

> 現在（Spot: 530）はHVL（520）上に位置しており、Positive GEX環境です。MMのデルタヘッジが価格安定方向に働いており、低ボラ・レンジ相場が続きやすい局面です。
>
> Transition Zone（Put Wall: 510〜Call Wall: 545）の中間付近に位置し、方向感は中立です。Call Wall（545）の強度（NETGEX: +800M）とPut Wall（510）の強度（NETGEX: −700M）を比較すると、両壁が均衡しており、当面はTransition Zone内でのレンジ継続が想定されます。
>
> Call Wall（545）を終値ベースで明確に突破した場合、次のCW2（560）まで急騰加速のリスクがあります。逆にPut Wall（510）を突破すると、Negative GEX転落により急落が加速する可能性に注意が必要です。

### Negative GEX環境の例文

> 現在（Spot: 508）はHVL（520）下に位置しており、Negative GEX環境です。MMのヘッジがトレンドを加速させる方向に働いており、高ボラ・トレンド相場になりやすい局面です。
>
> Spot（508）がPut Wall（502）に接近しており、Put Wall突破リスクが高まっています。Put Wall（502）を終値ベースで下抜けると、次のPW2（490）まで急落加速の可能性があります。
>
> 上方回復の条件としては、HVL（520）を上回りPositive GEX環境に転換することで、価格安定メカニズムが再稼働します。

---

## 付録: JSONデータ構造とフィールド対応

GEXプロファイルはJSONファイルとして出力されます（`3_extract_levels.py` → `data/levels/`）。

```json
{
  "ticker": "AAPL",
  "date": "2025-05-01",
  "spotPrice": 530.0,
  "totalGEX": -2500000000,          // ← マイナスでもCallWallは存在する！
  "sentiment": "positive_gamma",    // ← Spot(530) > HVL(520) だからPositive
  "levels": {
    "hvl": 520.0,                   // Gamma Flip Level
    "callWall": 545.0,              // 最強の上値抵抗（NETGEX最大の正ストライク）
    "putWall": 502.0,               // 最強の下値支持（NETGEX最小の負ストライク）
    "callWalls": [
      {"strike": 545.0, "netGEX": 800000000},   // Call Wall 1本目
      {"strike": 560.0, "netGEX": 300000000},   // Call Wall 2本目
      {"strike": 575.0, "netGEX": 150000000}    // Call Wall 3本目
    ],
    "putWalls": [
      {"strike": 502.0, "netGEX": -700000000},  // Put Wall 1本目
      {"strike": 490.0, "netGEX": -250000000},  // Put Wall 2本目
      {"strike": 480.0, "netGEX": -100000000}   // Put Wall 3本目
    ],
    "transition_zone": {"lower": 502.0, "upper": 545.0},
    "short_term": { /* 短期パネルと同じ構造 */ },
    "long_term":  { /* 長期パネルと同じ構造 */ }
  }
}
```

**totalGEXがマイナス（−2,500M）でもcallWall（545）に正のNETGEX（+800M）が存在することを構造で確認できます。**
