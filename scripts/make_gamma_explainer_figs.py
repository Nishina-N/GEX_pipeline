# -*- coding: utf-8 -*-
"""ガンマ解説ノート用の図を生成して Obsidian の literature notes/attachments に保存する。"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch

plt.rcParams["font.family"] = "Yu Gothic"
plt.rcParams["axes.unicode_minus"] = False

OUT = r"C:\Users\nishiha\Work\Obsidian\Obsidian Vault R2\literature notes\attachments"
os.makedirs(OUT, exist_ok=True)

GREEN = "#2e8b57"
RED = "#c0392b"
INK = "#222222"
AMBER = "#e08a1e"


# ── 図1: ガンマフリップ（HVL）で世界が反転 ───────────────────────────
def fig_flip():
    fig, ax = plt.subplots(figsize=(8, 6))
    # 価格を縦軸に。HVLを境に上=+γ(安定/谷), 下=-γ(不安定/山)
    ax.axhspan(5, 10, color=GREEN, alpha=0.10)
    ax.axhspan(0, 5, color=RED, alpha=0.10)
    ax.axhline(5, color=INK, ls="--", lw=2)
    ax.text(0.2, 5.15, "HVL（ガンマフリップ）", ha="left", va="bottom",
            fontsize=12, color=INK, fontweight="bold")

    # Call Wall / Put Wall
    ax.axhline(8.5, color=GREEN, lw=2)
    ax.text(0.2, 8.62, "Call Wall（上のかべ）", color=GREEN, fontsize=11, fontweight="bold")
    ax.axhline(1.5, color=RED, lw=2)
    ax.text(0.2, 1.62, "Put Wall（下のかべ）", color=RED, fontsize=11, fontweight="bold")

    # +γゾーンのボール（谷の底＝安定）
    ax.text(5, 7.0, "＋γ ゾーン（安定）", ha="center", fontsize=13, color=GREEN, fontweight="bold")
    ax.text(5, 6.3, "ボールはお椀の底に戻る\n＝値段は落ち着く・かべが効く", ha="center", fontsize=11, color=INK)

    # -γゾーン（山の上＝不安定）
    ax.text(5, 3.7, "−γ ゾーン（不安定）", ha="center", fontsize=13, color=RED, fontweight="bold")
    ax.text(5, 3.0, "ボールは坂を転がり落ちる\n＝値段は一方向に走りやすい", ha="center", fontsize=11, color=INK)

    ax.annotate("", xy=(7.2, 9.2), xytext=(7.2, 0.8),
                arrowprops=dict(arrowstyle="<->", color=INK, lw=1.5))
    ax.text(7.5, 5, "価格（上ほど高い）", rotation=90, ha="left", va="center",
            fontsize=10, color=INK)

    ax.set_xlim(0, 10); ax.set_ylim(0, 10)
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title("① HVL を境に「世界」が反転する", fontsize=15, fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "gamma_flip.png"), dpi=130)
    plt.close(fig)


# ── 図2: 谷（+γ）と山（-γ）のたとえ ───────────────────────────────
def fig_bowl_hill():
    fig, axes = plt.subplots(1, 2, figsize=(11, 5))
    x = np.linspace(-1, 1, 200)

    # 左: +γ = お椀（谷）
    ax = axes[0]
    ax.plot(x, x**2, color=GREEN, lw=3)
    ax.plot(0, 0.0, "o", color=AMBER, ms=18)
    ax.annotate("", xy=(-0.05, 0.02), xytext=(-0.55, 0.32),
                arrowprops=dict(arrowstyle="->", color=GREEN, lw=2))
    ax.annotate("", xy=(0.05, 0.02), xytext=(0.55, 0.32),
                arrowprops=dict(arrowstyle="->", color=GREEN, lw=2))
    ax.set_title("＋γ：お椀（谷）＝安定", fontsize=14, color=GREEN, fontweight="bold")
    ax.text(0, -0.18, "押してもボールは底に戻る\nMMは『逆張り』で値段を抑える", ha="center", fontsize=11)
    ax.set_xlim(-1.1, 1.1); ax.set_ylim(-0.3, 1.1); ax.axis("off")

    # 右: -γ = 山
    ax = axes[1]
    ax.plot(x, -(x**2) + 1, color=RED, lw=3)
    ax.plot(0, 1.0, "o", color=AMBER, ms=18)
    ax.annotate("", xy=(-0.55, 0.68), xytext=(-0.05, 0.98),
                arrowprops=dict(arrowstyle="->", color=RED, lw=2))
    ax.annotate("", xy=(0.55, 0.68), xytext=(0.05, 0.98),
                arrowprops=dict(arrowstyle="->", color=RED, lw=2))
    ax.set_title("−γ：山の上＝不安定", fontsize=14, color=RED, fontweight="bold")
    ax.text(0, -0.18, "少し押すと転がり落ちて加速\nMMは『順張り』で動きを増幅", ha="center", fontsize=11)
    ax.set_xlim(-1.1, 1.1); ax.set_ylim(-0.3, 1.1); ax.axis("off")

    fig.suptitle("② 同じ『押す力』でも、谷と山では結果が真逆", fontsize=15, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(os.path.join(OUT, "bowl_vs_hill.png"), dpi=130)
    plt.close(fig)


# ── 図3: MMヘッジの向き（逆張り vs 順張り） ──────────────────────────
def fig_hedge_direction():
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.2))

    def panel(ax, title, color, up_action, down_action, footer):
        ax.set_title(title, fontsize=14, color=color, fontweight="bold")
        # 上昇ケース
        ax.text(0.5, 0.86, "値段が上がる ⬆", ha="center", fontsize=12, fontweight="bold", color=INK)
        ax.text(0.5, 0.72, up_action, ha="center", fontsize=12, color=color, fontweight="bold")
        ax.axhline(0.62, xmin=0.1, xmax=0.9, color="#cccccc", lw=1)
        # 下落ケース
        ax.text(0.5, 0.48, "値段が下がる ⬇", ha="center", fontsize=12, fontweight="bold", color=INK)
        ax.text(0.5, 0.34, down_action, ha="center", fontsize=12, color=color, fontweight="bold")
        ax.text(0.5, 0.12, footer, ha="center", fontsize=11.5, color=INK,
                bbox=dict(boxstyle="round", fc=color, ec=color, alpha=0.12))
        ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
        for s in ["left", "right", "top", "bottom"]:
            ax.spines[s].set_visible(False)
        ax.add_patch(plt.Rectangle((0.02, 0.02), 0.96, 0.96, fill=False, ec=color, lw=2))

    panel(axes[0], "＋γ（壁の内側・安定）", GREEN,
          "MMは原資産を【売る】→ 上昇を抑える",
          "MMは原資産を【買う】→ 下落を支える",
          "結果：値段は中心に引き戻される（逆張り）\n＝Call/Put Wall がかべになる")
    panel(axes[1], "−γ（HVL下・不安定）", RED,
          "MMは原資産を【買う】→ 上昇をさらに押す",
          "MMは原資産を【売る】→ 下落をさらに押す",
          "結果：値段の動きが増幅される（順張り）\n＝かべを抜けると急騰・急落しやすい")

    fig.suptitle("③ MMヘッジの向きが、+γ と −γ で正反対になる", fontsize=15, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(os.path.join(OUT, "hedge_direction.png"), dpi=130)
    plt.close(fig)


# ── 図4: 3段階（満期）計算のしくみ ───────────────────────────────
def fig_three_stage():
    fig, ax = plt.subplots(figsize=(10, 5.6))
    base = 0.80
    # タイムライン（今日 → 満期）
    ax.axhline(base, color=INK, lw=2)
    ax.text(0.0, base + 0.05, "今日", fontsize=11, ha="left")
    ax.text(1.0, base + 0.02, "満期 →", ha="right", va="bottom", fontsize=10)
    days = [2, 5, 9, 16, 23, 51, 79]   # DTE 例
    for d in days:
        ax.plot(d / 90, base, "o", color=INK, ms=6)
    sq = [16, 51]   # 次の2回の月次SQ（第3金曜）の例

    # 短期：緑の矢印（タイムライン直下）
    y_s = 0.62
    ax.annotate("", xy=(0/90, y_s), xytext=(7/90, y_s),
                arrowprops=dict(arrowstyle="<->", color=GREEN, lw=2.5))
    ax.text(9/90, y_s, "短期：DTE 0〜7日（足元の圧力）", va="center",
            color=GREEN, fontsize=10, fontweight="bold")

    # 長期：黄色の矢印（緑と黒の中間）。今日〜次のSQまでを累積。SQ週は次の次のSQへロール
    y_l = 0.44
    ax.annotate("", xy=(sq[0]/90, y_l), xytext=(0/90, y_l),
                arrowprops=dict(arrowstyle="<->", color=AMBER, lw=2.5))
    ax.plot([sq[0]/90, sq[0]/90], [y_l, base], color=AMBER, ls=":", lw=1)  # 次のSQの位置
    # ロールオーバー（SQ目前の週）：次の次のSQまで点線で延長
    ax.annotate("", xy=(sq[1]/90, y_l), xytext=(sq[0]/90, y_l),
                arrowprops=dict(arrowstyle="->", color=AMBER, lw=1.2, linestyle=":"))
    ax.plot([sq[1]/90, sq[1]/90], [y_l, base], color=AMBER, ls=":", lw=1)
    ax.text(sq[1]/90 + 0.01, y_l, "SQ目前の週は\n次の次のSQまでロール", va="center",
            color=AMBER, fontsize=8.5)
    ax.text((0 + sq[0]) / 2 / 90, y_l - 0.10,
            "長期：今日〜次のSQまで累積（中期の岩盤）",
            ha="center", va="top", color=AMBER, fontsize=10, fontweight="bold")

    # 全満期：黒の矢印（最も外側）
    y_a = 0.16
    ax.annotate("", xy=(0/90, y_a), xytext=(79/90, y_a),
                arrowprops=dict(arrowstyle="<->", color=INK, lw=1.8))
    ax.text(40/90, y_a - 0.06, "全満期合算（左メインパネルのレベル線）",
            ha="center", va="top", color=INK, fontsize=10)

    ax.set_xlim(-0.02, 1.0); ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_title("④ このレポートの図は『満期で3段階』に分けて計算している", fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "three_stage.png"), dpi=130)
    plt.close(fig)


# ── 図5: 壁とレジームのペア（PWは−γの支持） ────────────────────────
def fig_wall_pairing():
    fig, ax = plt.subplots(figsize=(8.5, 6.5))
    ax.axhspan(5, 10, color=GREEN, alpha=0.10)
    ax.axhspan(0, 5, color=RED, alpha=0.10)

    # レベル線
    ax.axhline(8.5, color=GREEN, lw=2.5)
    ax.text(0.2, 8.62, "Call Wall（上のかべ）", color=GREEN, fontsize=11, fontweight="bold")
    ax.axhline(6.7, color=AMBER, lw=2)
    ax.text(0.2, 6.82, "Spot（今・＋γ）", color=AMBER, fontsize=11, fontweight="bold")
    ax.axhline(5.0, color=INK, ls="--", lw=2)
    ax.text(0.2, 5.12, "HVL（＋γ↔−γの境）", ha="left", color=INK, fontsize=11, fontweight="bold")
    ax.axhline(1.7, color=RED, lw=2.5)
    ax.text(0.2, 1.82, "Put Wall（下のかべ）", color=RED, fontsize=11, fontweight="bold")

    # ゾーン表示
    ax.text(8.2, 7.6, "＋γ ゾーン（安定）", color=GREEN, fontsize=12, fontweight="bold", ha="right")
    ax.text(8.2, 3.2, "−γ ゾーン（不安定）", color=RED, fontsize=12, fontweight="bold", ha="right")

    # Spotから下への矢印（HVLを割って-γへ）
    ax.add_patch(FancyArrowPatch((5.6, 6.7), (5.6, 2.0),
                 arrowstyle="-|>", mutation_scale=22, color=INK, lw=2))
    ax.text(5.85, 3.8, "下落するとHVLを割る\n＝この時点で−γに反転", fontsize=10.5, va="center", color=INK)

    # ＋γで近づく壁
    ax.annotate("＋γで近づくのは Call Wall（自然なペア）", xy=(6.7, 8.5), xytext=(2.6, 9.4),
                fontsize=10.5, color=GREEN,
                arrowprops=dict(arrowstyle="->", color=GREEN, lw=1.5))
    # PWは-γの支持
    ax.text(3.0, 0.7, "Put Wall に届く頃にはもう −γ\n＝Put Wall は『−γの世界の支持線』",
            fontsize=10.5, color=RED, fontweight="bold")

    ax.set_xlim(0, 10); ax.set_ylim(0, 10)
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title("⑤ ＋γで下げると、Put Wall到達前にHVLで−γへ反転する", fontsize=13.5, fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "wall_regime_pairing.png"), dpi=130)
    plt.close(fig)


# ── 図6: GEXは「動く地形」（ガンマのスポットライト） ───────────────
def fig_dynamic():
    strikes = np.array([732, 735, 738, 741, 744, 747, 750, 753, 756])
    netgex = np.array([-3.0, -4.5, -3.5, -1.5, 0.2, 1.5, 2.8, 1.8, 0.8])
    hvl = 744.0

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 6), sharey=True)

    def panel(ax, spot, title, sign_text, sign_color):
        colors = [GREEN if v >= 0 else RED for v in netgex]
        ax.barh(strikes, netgex, height=2.2, color=colors, alpha=0.85)
        ax.axvline(0, color=INK, lw=1)
        ax.axhline(hvl, color=INK, ls="--", lw=1.8)
        ax.text(-4.7, hvl + 0.3, "HVL", fontsize=10, fontweight="bold", color=INK)
        # いま効いている帯（価格の近くほどガンマが強い）
        ax.axhspan(spot - 3, spot + 3, color=AMBER, alpha=0.18)
        ax.axhline(spot, color=AMBER, lw=2.5)
        ax.text(4.8, spot, f"Spot {spot:.0f}", color=AMBER, fontsize=10.5,
                fontweight="bold", ha="right", va="bottom")
        ax.text(4.8, spot - 1.4, "← いま効く帯\n   （価格の近く）", color=AMBER,
                fontsize=9, ha="right", va="top")
        ax.text(0, 758.5, sign_text, ha="center", fontsize=13, color=sign_color,
                fontweight="bold")
        ax.set_title(title, fontsize=13, fontweight="bold")
        ax.set_xlim(-5, 5); ax.set_ylim(729, 760)
        ax.set_xlabel("← プット優勢（−）   NETGEX   コール優勢（＋）→", fontsize=9)
        ax.set_xticks([])

    panel(axes[0], 752, "価格が高い：効く帯が＋側", "totalGEX ＝ ＋（＋γ・安定）", GREEN)
    panel(axes[1], 740, "価格が下がる：効く帯が−側へ", "totalGEX ＝ −（−γ・加速）", RED)
    axes[0].set_ylabel("価格（ストライク）", fontsize=10)

    fig.suptitle("⑥ GEXは『動く地形』：価格が動くと totalGEX も変わる", fontsize=15, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(os.path.join(OUT, "gex_dynamic.png"), dpi=130)
    plt.close(fig)


# ── 図7: デルタとガンマ（なぜ売り買いの向きが決まるか） ────────────
def fig_delta_gamma():
    fig, ax = plt.subplots(figsize=(9, 5.8))
    price = np.linspace(90, 110, 200)
    delta = 1 / (1 + np.exp(-(price - 100) / 2.2))  # コールのデルタ（S字）
    ax.plot(price, delta, color=INK, lw=3)

    # 2点：価格98→102でデルタが増える
    for p, col, lab in [(98, RED, "価格 98"), (102, GREEN, "価格 102")]:
        d = 1 / (1 + np.exp(-(p - 100) / 2.2))
        ax.plot([p, p], [0, d], color=col, ls=":", lw=1.5)
        ax.plot([90, p], [d, d], color=col, ls=":", lw=1.5)
        ax.plot(p, d, "o", color=col, ms=10)
        ax.text(p, -0.06, lab, color=col, ha="center", fontsize=10, fontweight="bold")
        ax.text(89.6, d, f"{d:.2f}", color=col, ha="right", va="center", fontsize=10, fontweight="bold")

    ax.annotate("価格が上がると\nデルタが増える\n（＝ガンマ）", xy=(101, 0.62), xytext=(103.4, 0.33),
                fontsize=10.5, color=INK,
                arrowprops=dict(arrowstyle="->", color=INK, lw=1.5))

    ax.text(90.2, 1.02,
            "デルタ＝『今この株を何株分持っているのと同じか』（0〜1）\nガンマ＝価格が動いたときのデルタの変化（カーブの傾き）",
            fontsize=10, color=INK, va="bottom")

    ax.set_xlim(89, 111); ax.set_ylim(-0.12, 1.18)
    ax.set_xlabel("株価 →", fontsize=11)
    ax.set_ylabel("デルタ（株数の感じ）", fontsize=11)
    ax.set_yticks([0, 0.5, 1.0])
    ax.set_title("⑦ デルタとガンマ：価格が動くと『持っている株数の感じ』が変わる", fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "delta_gamma.png"), dpi=130)
    plt.close(fig)


# ── 図7b: ＋γ と −γ でデルタの動きが逆 ───────────────────────────
def fig_delta_pm():
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 5.2), sharey=True)
    price = np.linspace(90, 110, 200)
    up = 1 / (1 + np.exp(-(price - 100) / 2.2))      # ＋γ：価格↑でデルタ↑
    down = 1 - up                                      # −γ：価格↑でデルタ↓

    def panel(ax, curve, color, title, up_txt, down_txt):
        ax.plot(price, curve, color=color, lw=3)
        ax.axvline(100, color="#bbbbbb", ls=":", lw=1)
        ax.annotate("", xy=(104, np.interp(104, price, curve)),
                    xytext=(96, np.interp(96, price, curve)),
                    arrowprops=dict(arrowstyle="->", color=color, lw=2))
        ax.set_title(title, fontsize=13, color=color, fontweight="bold")
        ax.text(91, 1.14, up_txt, fontsize=10.5, color=color, va="top")
        ax.text(91, 1.04, down_txt, fontsize=10.5, color=color, va="top")
        ax.set_xlim(89, 111); ax.set_ylim(-0.05, 1.30)
        ax.set_xlabel("株価 →", fontsize=10)
        ax.set_yticks([0, 0.5, 1.0])

    panel(axes[0], up, GREEN, "＋γ：価格と同じ向き",
          "価格↑ → デルタ↑ → 売る（上昇を抑える）",
          "価格↓ → デルタ↓ → 買う（下落を支える）")
    panel(axes[1], down, RED, "−γ：価格と逆向き（さかさま）",
          "価格↑ → デルタ↓ → 買う（上昇を押す）",
          "価格↓ → デルタ↑ → 売る（下落を押す）")
    axes[0].set_ylabel("お店のデルタ（株数の感じ）", fontsize=10)

    fig.suptitle("⑦b ＋γ と −γ では、価格に対するデルタの動きが逆になる", fontsize=14, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(os.path.join(OUT, "delta_pm.png"), dpi=130)
    plt.close(fig)


fig_flip()
fig_bowl_hill()
fig_hedge_direction()
fig_three_stage()
fig_wall_pairing()
fig_dynamic()
fig_delta_gamma()
fig_delta_pm()
print("done ->", OUT)
for f in os.listdir(OUT):
    print(" ", f)
