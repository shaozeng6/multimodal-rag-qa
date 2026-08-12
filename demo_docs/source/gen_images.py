# -*- coding: utf-8 -*-
"""生成演示文档配图(云杉 SS-350 虚构产品, 仅供多模态 RAG 演示)。

用法(任意装有 matplotlib 的 Python):
    python gen_images.py
产物写入 demo_docs/source/images/ 下 4 张 PNG。

图中数据与文档正文表格保持一致(如冰萃模式 88℃/12 分钟), 便于演示图文交叉问答。
"""
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle

# ---- 中文字体(Windows) ----
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DengXian"]
plt.rcParams["axes.unicode_minus"] = False
_available = {f.name for f in font_manager.fontManager.ttflist}
for _cand in plt.rcParams["font.sans-serif"]:
    if _cand in _available:
        print("使用中文字体:", _cand)
        break
else:
    print("警告: 未找到候选中文字体, 图内中文可能显示为方块")

ACCENT = "#2F6F62"   # 云杉绿
ACCENT_LT = "#DCEBE7"
DARK = "#22313A"
GRAY = "#8A9794"
GLASS = "#CFE6EF"
WATER = "#9FC9E8"

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
os.makedirs(OUT, exist_ok=True)


def _callout(ax, num, xy_label, xy_part, side="left"):
    """带引线的编号标注。"""
    ax.annotate(
        "", xy=xy_part, xytext=xy_label,
        arrowprops=dict(arrowstyle="-", color=DARK, lw=0.9, shrinkA=6, shrinkB=2),
    )
    c = Circle(xy_label, 0.19, fc="white", ec=ACCENT, lw=1.6, zorder=5)
    ax.add_patch(c)
    ax.text(xy_label[0], xy_label[1], str(num), ha="center", va="center",
            fontsize=10, color=ACCENT, fontweight="bold", zorder=6)


def fig1_structure():
    """图1: 产品结构示意图(编号部件 + 引线)。"""
    fig, ax = plt.subplots(figsize=(8.8, 6.2))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 7)
    ax.axis("off")

    # 地面
    ax.plot([1.4, 8.6], [0.62, 0.62], color=GRAY, lw=1.2)

    # ⑥ 加热底座
    ax.add_patch(FancyBboxPatch((2.15, 0.62), 3.9, 0.42,
                                boxstyle="round,pad=0.02", fc=DARK, ec="none"))
    # 主机右塔(含水箱腔)
    ax.add_patch(FancyBboxPatch((4.55, 1.0), 1.65, 5.05,
                                boxstyle="round,pad=0.03", fc="#F4F6F5", ec=DARK, lw=1.4))
    # ① 水箱视窗
    ax.add_patch(Rectangle((4.82, 3.35), 1.1, 2.35, fc="white", ec=DARK, lw=1.1))
    ax.add_patch(Rectangle((4.82, 3.35), 1.1, 1.55, fc=WATER, ec="none"))
    ax.plot([4.9, 5.15, 5.4, 5.65, 5.85], [4.9, 4.82, 4.9, 4.82, 4.9],
            color="#6FA8D6", lw=1.2)
    # 水位刻度
    for y in (3.8, 4.3, 4.8, 5.3):
        ax.plot([5.92, 6.05], [y, y], color=GRAY, lw=0.8)

    # ② 顶部注水臂 + 淋浴喷头
    ax.add_patch(FancyBboxPatch((2.55, 5.42), 2.1, 0.42,
                                boxstyle="round,pad=0.02", fc=ACCENT_LT, ec=DARK, lw=1.2))
    ax.add_patch(Rectangle((2.72, 5.06), 0.7, 0.36, fc=DARK, ec="none"))
    for dx in (0.12, 0.35, 0.58):
        ax.plot([2.72 + dx, 2.72 + dx], [5.0, 4.86], color=WATER, lw=1.4)

    # ③ 滤杯(梯形)
    ax.add_patch(Polygon([(2.35, 4.62), (3.85, 4.62), (3.52, 3.85), (2.68, 3.85)],
                         closed=True, fc="#EFE7DA", ec=DARK, lw=1.3))
    ax.add_patch(Rectangle((2.9, 3.62), 0.4, 0.23, fc=DARK, ec="none"))  # 滴口

    # ④ 玻璃咖啡壶
    ax.add_patch(FancyBboxPatch((2.45, 1.12), 2.05, 2.3,
                                boxstyle="round,pad=0.06", fc=GLASS, ec=DARK, lw=1.3))
    ax.add_patch(Rectangle((2.62, 1.28), 1.7, 0.9, fc="#B5773F", ec="none", alpha=0.55))
    ax.add_patch(Circle((2.28, 2.3), 0.34, fill=False, ec=DARK, lw=1.6))  # 壶把(外侧)

    # ⑤ 控制面板
    ax.add_patch(Rectangle((4.82, 1.35), 1.1, 1.65, fc="white", ec=DARK, lw=1.1))
    ax.add_patch(Rectangle((4.95, 2.42), 0.84, 0.42, fc=DARK, ec="none"))   # 屏幕
    ax.add_patch(Circle((5.37, 1.85), 0.26, fc=ACCENT_LT, ec=DARK, lw=1.1))  # 旋钮

    # 编号引线(1 号水箱标在右侧, 避免与左侧引线交叉)
    _callout(ax, 1, (7.9, 4.6), (5.92, 4.6))
    _callout(ax, 2, (0.95, 5.6), (2.7, 5.63))
    _callout(ax, 3, (0.95, 3.9), (2.42, 4.3))
    _callout(ax, 4, (0.95, 1.6), (2.5, 1.75))
    _callout(ax, 5, (7.9, 2.2), (5.92, 2.2))
    _callout(ax, 6, (7.9, 0.84), (6.05, 0.84))

    fig.savefig(os.path.join(OUT, "fig1_structure.png"), dpi=170,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("fig1_structure.png done")


def fig2_first_use_flow():
    """图2: 首次使用流程图(6 步横向流程)。"""
    steps = [
        ("开箱检查", "核对包装清单"),
        ("清洗配件", "温水冲洗壶/滤杯"),
        ("水箱加水", "至 MAX 水位线"),
        ("通电联网", "App 配对 Wi-Fi"),
        ("自清洁", "空壶运行一次"),
        ("开始冲煮", "加粉并选择模式"),
    ]
    fig, ax = plt.subplots(figsize=(9.6, 2.9))
    ax.set_xlim(0, 12.4)
    ax.set_ylim(0, 3.6)
    ax.axis("off")

    x0, bw, bh, gap = 0.25, 1.78, 1.35, 0.24
    y0 = 1.15
    for i, (title, sub) in enumerate(steps):
        x = x0 + i * (bw + gap)
        ax.add_patch(FancyBboxPatch((x, y0), bw, bh,
                                    boxstyle="round,pad=0.04",
                                    fc=ACCENT_LT if i % 2 == 0 else "white",
                                    ec=ACCENT, lw=1.5))
        ax.text(x + 0.16, y0 + bh - 0.24, str(i + 1), fontsize=9.5,
                color="white", ha="center", va="center", fontweight="bold",
                bbox=dict(boxstyle="circle,pad=0.18", fc=ACCENT, ec="none"))
        ax.text(x + bw / 2, y0 + bh / 2 + 0.08, title, ha="center", va="center",
                fontsize=11.5, color=DARK, fontweight="bold")
        ax.text(x + bw / 2, y0 + 0.3, sub, ha="center", va="center",
                fontsize=8.3, color=GRAY)
        if i < len(steps) - 1:
            ax.add_patch(FancyArrowPatch((x + bw + 0.02, y0 + bh / 2),
                                         (x + bw + gap - 0.02, y0 + bh / 2),
                                         arrowstyle="-|>", mutation_scale=14,
                                         color=ACCENT, lw=1.6))
    fig.savefig(os.path.join(OUT, "fig2_first_use_flow.png"), dpi=170,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("fig2_first_use_flow.png done")


def fig3_mode_chart():
    """图3: 四种冲煮模式参数对比(与正文模式表数据一致)。"""
    modes = ["标准", "浓郁", "冰萃", "自定义"]
    temp = [92, 94, 88, 90]      # 水温 ℃
    minutes = [6, 8, 12, 5]      # 冲煮时长 min

    import numpy as np
    x = np.arange(len(modes))
    w = 0.36
    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    b1 = ax.bar(x - w / 2, temp, w, label="水温 (℃)", color=ACCENT)
    b2 = ax.bar(x + w / 2, minutes, w, label="冲煮时长 (分钟)", color="#D98E32")
    ax.bar_label(b1, padding=2, fontsize=9.5)
    ax.bar_label(b2, padding=2, fontsize=9.5)
    ax.set_xticks(x)
    ax.set_xticklabels(modes, fontsize=11)
    ax.set_ylabel("数值", fontsize=10.5)
    ax.set_ylim(0, 105)
    ax.set_title("四种冲煮模式参数对比", fontsize=13, color=DARK, pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.legend(fontsize=10, frameon=False, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    fig.savefig(os.path.join(OUT, "fig3_mode_chart.png"), dpi=170,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("fig3_mode_chart.png done")


def fig4_service_chart():
    """图4: 各售后渠道平均首次响应时长(与售后手册正文一致)。"""
    import numpy as np
    channels = ["在线客服", "400 热线", "微信售后", "门店服务"]
    minutes = [1.5, 3.2, 8.4, 25]
    order = np.argsort(minutes)[::-1]
    channels = [channels[i] for i in order]
    minutes = [minutes[i] for i in order]

    fig, ax = plt.subplots(figsize=(7.6, 3.6))
    bars = ax.barh(channels, minutes, color=[ACCENT, ACCENT, "#D98E32", GRAY])
    ax.bar_label(bars, padding=4, fontsize=10, fmt="%.1f 分钟")
    ax.set_xlim(0, 30)
    ax.set_xlabel("平均首次响应时长(分钟)", fontsize=10.5)
    ax.set_title("各售后渠道平均首次响应时长(2026 上半年)", fontsize=12.5, color=DARK, pad=10)
    ax.invert_yaxis()
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", linestyle="--", alpha=0.35)
    fig.savefig(os.path.join(OUT, "fig4_service_chart.png"), dpi=170,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("fig4_service_chart.png done")


if __name__ == "__main__":
    fig1_structure()
    fig2_first_use_flow()
    fig3_mode_chart()
    fig4_service_chart()
    print("全部图片已生成 ->", OUT)
