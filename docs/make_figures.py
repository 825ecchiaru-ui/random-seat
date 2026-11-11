#生成docs/algorithm.md中使用的全部图例，输出为docs/figures下的SVG文件
#环境中没有matplotlib，这里直接拼写SVG；图中的数据全部来自实际运行主程序和模拟
#用法：python docs/make_figures.py    （约需1~2分钟，主要耗时在模拟上）
import sys
import math
import random
import statistics
from multiprocessing import Pool
from pathlib import Path

import numpy as np

DOCS_DIR = Path(__file__).resolve().parent
FIG_DIR = DOCS_DIR / "figures"
sys.path.insert(0, str(DOCS_DIR.parent / "tests"))

from layout_tools import m, example_layouts, build
from sim_weight_design import make_matrix, run

#配色：图面、文字、坐标轴的颜色，以及三个对比方案各自固定的颜色
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BLUE, ORANGE, AQUA, RED = "#2a78d6", "#eb6834", "#1baf7a", "#e34948"
NEUTRAL = "#f0efec"
FONT = 'system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif'

#三个对比方案：名称、颜色、模拟参数(design, 加权候选数, 纯随机候选数)
OLD_DESIGN = ("sum", "inv", 1, 1, 1, 1, "all")
PLANS = [
    ("只用纯随机候选", AQUA, ("main", 0, 100)),
    ("原设计（线性加和）", BLUE, (OLD_DESIGN, 50, 50)),
    ("现设计（指数形式）", ORANGE, ("main", 50, 50)),
]
ROUNDS = 20
SEEDS = 12


# ---------- SVG小工具 ----------

def svg_open(width, height):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
            f'height="{height}" font-family=\'{FONT}\'>',
            f'<rect width="{width}" height="{height}" fill="{SURFACE}"/>']


def text(x, y, content, size=12, fill=INK_2, anchor="start", weight="normal"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{weight}">{content}</text>')


def mix(color_a, color_b, t):
    #在两个十六进制颜色之间线性插值，t=0得到color_a，t=1得到color_b
    a = [int(color_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(color_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(a[k] + (b[k] - a[k]) * t):02x}" for k in range(3))


def save(name, parts):
    FIG_DIR.mkdir(exist_ok=True)
    (FIG_DIR / name).write_text("\n".join(parts + ["</svg>"]), encoding="utf-8")
    print("已生成", name)


def legend(parts, x, y, items):
    #横向图例：每项是一个色块加名称
    for name, color in items:
        parts.append(f'<rect x="{x}" y="{y - 9}" width="10" height="10" rx="2" fill="{color}"/>')
        parts.append(text(x + 15, y, name, 12, INK_2))
        x += 15 + 12.5 * len(name) + 22


# ---------- 图1：座位负担值 ----------

def seat_grid(parts, positions, values, x0, y0, cell_w, cell_h, color_of, label_of):
    #按座位坐标画出教室俯视图。x（前后）自上而下，y（左右）自左向右，黑板在最上方
    ys = [p[1] for p in positions]
    y_min, y_max = min(ys), max(ys)
    board_w = (y_max - y_min + 1) * cell_w
    parts.append(f'<rect x="{x0}" y="{y0}" width="{board_w - 2}" height="8" rx="2" fill="{INK_2}"/>')
    parts.append(text(x0 + board_w / 2, y0 - 6, "黑板", 11, MUTED, "middle"))
    for (x, y), value in zip(positions, values):
        cx = x0 + (y - y_min) * cell_w
        cy = y0 + 20 + (x - 1) * cell_h
        fill, ink = color_of(value)
        parts.append(f'<rect x="{cx}" y="{cy}" width="{cell_w - 2}" height="{cell_h - 2}" rx="4" fill="{fill}"/>')
        label = label_of(value)
        if label:
            parts.append(text(cx + (cell_w - 2) / 2, cy + (cell_h - 2) / 2 + 4, label, 11, ink, "middle"))
    return board_w


def burden_color(value):
    #发散色：负担为负（好座位）用蓝色，为正（差座位）用红色，0附近为中性灰
    fill = mix(NEUTRAL, BLUE if value < 0 else RED, min(abs(value), 1.0))
    return fill, ("#ffffff" if abs(value) > 0.6 else INK)


def figure_burden():
    positions = example_layouts()["example.csv"]
    classroom, _, _ = build(positions, 0)
    values = [seat.burden for seat in classroom.seats]

    cell_w, cell_h = 50, 36
    width, height = 60 + 12 * cell_w, 388
    parts = svg_open(width, height)
    parts.append(text(30, 28, "example.csv布局中各座位的负担值", 15, INK, weight="600"))
    parts.append(text(30, 47, "越靠近全部座位的几何中心负担越小（−1），越远越大（+1）", 12, INK_2))
    seat_grid(parts, positions, values, 30, 78, cell_w, cell_h, burden_color,
              lambda v: f"{v:+.2f}".replace("-", "−"))

    #色标
    bar_x, bar_y, bar_w = 30, 360, 240
    for k in range(48):
        v = -1 + 2 * (k + 0.5) / 48
        parts.append(f'<rect x="{bar_x + k * bar_w / 48:.1f}" y="{bar_y}" width="{bar_w / 48 + 0.5:.1f}" '
                     f'height="10" fill="{burden_color(v)[0]}"/>')
    parts.append(text(bar_x, bar_y - 6, "−1 好座位", 11, MUTED))
    parts.append(text(bar_x + bar_w / 2, bar_y - 6, "0", 11, MUTED, "middle"))
    parts.append(text(bar_x + bar_w, bar_y - 6, "差座位 +1", 11, MUTED, "end"))
    save("burden_map.svg", parts)


# ---------- 图2：H项随负担偏差的变化 ----------

def figure_history_term():
    width, height = 620, 330
    left, right, top, bottom = 60, 30, 70, 50
    plot_w, plot_h = width - left - right, height - top - bottom
    h_min, h_max = -1.5, 1.5

    def px(h):
        return left + (h - h_min) / (h_max - h_min) * plot_w

    def py(v):
        return top + (1 - v) * plot_h

    parts = svg_open(width, height)
    parts.append(text(left - 30, 28, "历史负担项对权重的影响", 15, INK, weight="600"))
    parts.append(text(left - 30, 47, f"纵轴为权重中由H贡献的倍数exp(c·H)，c = {m.config.WEIGHT_HISTORY:g}", 12, INK_2))
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        parts.append(f'<line x1="{left}" y1="{py(v):.1f}" x2="{left + plot_w}" y2="{py(v):.1f}" '
                     f'stroke="{AXIS if v == 0 else GRID}" stroke-width="1"/>')
        parts.append(text(left - 8, py(v) + 4, f"{v:g}", 11, MUTED, "end"))
    for h in (-1.5, -1, -0.5, 0, 0.5, 1, 1.5):
        parts.append(text(px(h), top + plot_h + 18, f"{h:g}".replace("-", "−"), 11, MUTED, "middle"))
    parts.append(text(left + plot_w / 2, height - 10, "h：换座后的历史负担相对全班平均的偏差", 12, INK_2, "middle"))

    points = []
    for k in range(301):
        h = h_min + (h_max - h_min) * k / 300
        points.append(f"{px(h):.1f},{py(math.exp(m.config.WEIGHT_HISTORY * -max(h, 0))):.1f}")
    parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{BLUE}" stroke-width="2" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>')
    parts.append(text(px(-0.75), py(1) + 20, "低于平均：不扣分，权重不变", 12, INK_2, "middle"))
    parts.append(text(px(0.42), py(0.42), "高于平均：权重迅速下降", 12, INK_2))
    for h in (0.25, 0.5):
        v = math.exp(m.config.WEIGHT_HISTORY * -h)
        parts.append(f'<circle cx="{px(h):.1f}" cy="{py(v):.1f}" r="4" fill="{BLUE}" stroke="{SURFACE}" '
                     f'stroke-width="2"/>')
        parts.append(text(px(h) + 9, py(v) - 6, f"h = {h:g} 时为 {v:.2f}", 11, INK_2))
    save("history_term.svg", parts)


# ---------- 图3：两名学生的权重分布 ----------

def weight_color(value):
    #顺序色：权重越大蓝色越深，接近0时退到接近图面的浅色
    stops = ["#eef3f9", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
    pos = min(max(value, 0.0), 1.0) * (len(stops) - 1)
    k = min(int(pos), len(stops) - 2)
    return mix(stops[k], stops[k + 1], pos - k), INK


def figure_weights():
    #在example.csv满员教室中先排座6轮，再取历史负担最高和最低的两名学生，画出他们本轮的权重
    positions = example_layouts()["example.csv"]
    random.seed(7)
    classroom, students, alloc = build(positions, len(positions))
    for _ in range(6):
        alloc.apply_assignment(alloc.select_best_assignment()[0])
    weights = alloc.calculate_weights()
    mean_accum = statistics.fmean(s.accum_burden for s in students)
    high = max(students, key=lambda s: s.accum_burden)
    low = min(students, key=lambda s: s.accum_burden)

    cell_w, cell_h = 36, 30
    panel_w = 12 * cell_w
    width, height = 60 + 2 * panel_w + 50, 372
    parts = svg_open(width, height)
    parts.append(text(30, 28, "同一轮中两名学生对各座位的权重", 15, INK, weight="600"))
    parts.append(text(30, 47, "颜色越深越容易被抽中；带黑框的是该学生当前的座位", 12, INK_2))

    for k, (student, title) in enumerate(((high, "历史负担最高的学生"), (low, "历史负担最低的学生"))):
        x0 = 30 + k * (panel_w + 50)
        row = weights[students.index(student)]
        offset = student.accum_burden - mean_accum
        parts.append(text(x0, 82, f"{title}（比全班平均{'高' if offset > 0 else '低'} {abs(offset):.2f}）",
                          13, INK, weight="600"))
        seat_grid(parts, positions, row, x0, 112, cell_w, cell_h, weight_color, lambda v: "")
        #标出当前座位
        cur = student.current_seat
        y_min = min(p[1] for p in positions)
        cx = x0 + (cur.y - y_min) * cell_w
        cy = 112 + 20 + (cur.x - 1) * cell_h
        parts.append(f'<rect x="{cx + 1}" y="{cy + 1}" width="{cell_w - 4}" height="{cell_h - 4}" rx="3" '
                     f'fill="none" stroke="{INK}" stroke-width="2"/>')

    #色标
    bar_x, bar_y, bar_w = 30, 346, 240
    for k in range(48):
        parts.append(f'<rect x="{bar_x + k * bar_w / 48:.1f}" y="{bar_y}" width="{bar_w / 48 + 0.5:.1f}" '
                     f'height="10" fill="{weight_color((k + 0.5) / 48)[0]}"/>')
    parts.append(text(bar_x, bar_y - 6, "权重 0", 11, MUTED))
    parts.append(text(bar_x + bar_w, bar_y - 6, "1（该学生最偏好的座位）", 11, MUTED, "end"))
    save("weight_maps.svg", parts)


# ---------- 图4：三种方案的公平性对比 ----------

def collect_comparison():
    #对三个方案分别运行完整模拟，返回{(方案名, 入座率): {指标名: 平均值}}
    names = ("var", "range", "move", "board", "stay", "bad2x", "occ", "pick_random")
    table = {}
    for name, _, (design, n_weighted, n_random) in PLANS:
        results = run([design], (1.0, 0.85, 0.7), SEEDS, n_weighted=n_weighted, n_random=n_random)
        for fill in (1.0, 0.85, 0.7):
            rows = [r for r in results if r[1] == fill]
            table[(name, fill)] = {key: statistics.fmean(r[4 + i] for r in rows) for i, key in enumerate(names)}
    return table


def bar_panel(parts, table, key, title, x0, y0, plot_w, plot_h, y_max, y_step):
    def py(v):
        return y0 + plot_h - v / y_max * plot_h

    parts.append(text(x0, y0 - 16, title, 13, INK, weight="600"))
    v = 0.0
    while v <= y_max + 1e-9:
        parts.append(f'<line x1="{x0}" y1="{py(v):.1f}" x2="{x0 + plot_w}" y2="{py(v):.1f}" '
                     f'stroke="{AXIS if v == 0 else GRID}" stroke-width="1"/>')
        parts.append(text(x0 - 8, py(v) + 4, f"{v:g}", 11, MUTED, "end"))
        v += y_step

    fills = ((1.0, "满员"), (0.85, "85%入座"), (0.7, "70%入座"))
    band = plot_w / len(fills)
    bar_w, gap = 24, 2
    for g, (fill, label) in enumerate(fills):
        start = x0 + g * band + (band - (3 * bar_w + 2 * gap)) / 2
        for k, (name, color, _) in enumerate(PLANS):
            value = table[(name, fill)][key]
            bx, top = start + k * (bar_w + gap), py(value)
            #柱子：顶端圆角4px，底端贴着基线保持直角
            parts.append(f'<path d="M{bx:.1f},{py(0):.1f} V{top + 4:.1f} Q{bx:.1f},{top:.1f} {bx + 4:.1f},{top:.1f} '
                         f'H{bx + bar_w - 4:.1f} Q{bx + bar_w:.1f},{top:.1f} {bx + bar_w:.1f},{top + 4:.1f} '
                         f'V{py(0):.1f} Z" fill="{color}"/>')
            parts.append(text(bx + bar_w / 2, top - 5, f"{value:.2f}", 10, INK_2, "middle"))
        parts.append(text(x0 + g * band + band / 2, y0 + plot_h + 18, label, 12, INK_2, "middle"))


def figure_comparison(table):
    width, height = 820, 370
    parts = svg_open(width, height)
    parts.append(text(30, 28, f"连续排座{ROUNDS}轮后全班历史负担的方差与极差", 15, INK, weight="600"))
    parts.append(text(30, 47, f"数值越小越公平；examples下5个布局、每种情况{SEEDS}次模拟的平均值", 12, INK_2))
    legend(parts, 30, 74, [(name, color) for name, color, _ in PLANS])
    bar_panel(parts, table, "var", "方差", 60, 126, 320, 200, 0.8, 0.2)
    bar_panel(parts, table, "range", "极差", 470, 126, 320, 200, 4.0, 1.0)
    save("fairness_comparison.svg", parts)


# ---------- 图5：方差随轮次的变化 ----------

def variance_by_round(args):
    #一次模拟：返回每一轮结束后全班历史负担的方差列表
    design, n_weighted, n_random, positions, seed = args
    random.seed(seed)
    classroom, students, alloc = build(positions, len(positions))
    variances = []
    for _ in range(ROUNDS + 1):
        candidates = []
        if n_weighted:
            w = alloc.calculate_weights() if design == "main" else make_matrix(alloc, design)
            candidates += [alloc.generate_assignment(w) for _ in range(n_weighted)]
        candidates += [alloc.random_assignment() for _ in range(n_random)]
        scores = [alloc.score_assignment(c) for c in candidates]
        alloc.apply_assignment(candidates[scores.index(max(scores))])
        variances.append(statistics.pvariance(s.accum_burden for s in students))
    return variances


def figure_rounds():
    layouts = example_layouts()
    series = []
    with Pool(6) as pool:
        for name, color, (design, n_weighted, n_random) in PLANS:
            jobs = [(design, n_weighted, n_random, pos, seed) for pos in layouts.values() for seed in range(SEEDS)]
            runs = pool.map(variance_by_round, jobs, chunksize=4)
            series.append((name, color, [statistics.fmean(r[k] for r in runs) for k in range(ROUNDS + 1)]))

    width, height = 700, 380
    left, right, top, bottom = 60, 170, 100, 50
    plot_w, plot_h = width - left - right, height - top - bottom
    y_max = 0.8

    def px(k):
        return left + k / ROUNDS * plot_w

    def py(v):
        return top + (1 - v / y_max) * plot_h

    parts = svg_open(width, height)
    parts.append(text(30, 28, "满员时全班历史负担的方差随排座轮次的变化", 15, INK, weight="600"))
    parts.append(text(30, 47, f"5个布局、每个布局{SEEDS}次模拟的平均值；第0轮为首次排座", 12, INK_2))
    legend(parts, 30, 74, [(name, color) for name, color, _ in PLANS])
    for v in (0, 0.2, 0.4, 0.6, 0.8):
        parts.append(f'<line x1="{left}" y1="{py(v):.1f}" x2="{left + plot_w}" y2="{py(v):.1f}" '
                     f'stroke="{AXIS if v == 0 else GRID}" stroke-width="1"/>')
        parts.append(text(left - 8, py(v) + 4, f"{v:g}", 11, MUTED, "end"))
    for k in range(0, ROUNDS + 1, 5):
        parts.append(text(px(k), top + plot_h + 18, str(k), 11, MUTED, "middle"))
    parts.append(text(left + plot_w / 2, height - 10, "排座轮次", 12, INK_2, "middle"))

    for name, color, values in series:
        points = " ".join(f"{px(k):.1f},{py(v):.1f}" for k, v in enumerate(values))
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
        parts.append(f'<circle cx="{px(ROUNDS):.1f}" cy="{py(values[-1]):.1f}" r="4" fill="{color}" '
                     f'stroke="{SURFACE}" stroke-width="2"/>')
    #线尾标注：前两条线在末端很接近，把它们的标注上下错开并保持与线尾对齐
    ends = sorted(series, key=lambda s: -s[2][-1])
    label_y = [py(s[2][-1]) + 4 for s in ends]
    for k in range(1, len(label_y)):
        label_y[k] = max(label_y[k], label_y[k - 1] + 30)
    for (name, color, values), y in zip(ends, label_y):
        parts.append(f'<line x1="{px(ROUNDS) + 6:.1f}" y1="{py(values[-1]):.1f}" x2="{px(ROUNDS) + 16:.1f}" '
                     f'y2="{y - 4:.1f}" stroke="{AXIS}" stroke-width="1"/>')
        parts.append(text(px(ROUNDS) + 20, y, name, 11, INK_2))
        parts.append(text(px(ROUNDS) + 20, y + 14, f"{values[-1]:.2f}", 11, INK, weight="600"))
    save("variance_by_round.svg", parts)
    return series


if __name__ == "__main__":
    figure_burden()
    figure_history_term()
    figure_weights()
    table = collect_comparison()
    figure_comparison(table)
    series = figure_rounds()

    #打印文档中表格要用的数字
    print("\n方案 / 入座率 / 方差 / 极差 / 移动距离 / 黑板距离变化 / 留原座 / 连续差座位 / 已坐座位平均负担 / 选中纯随机方案")
    for (name, fill), row in table.items():
        print(f"{name} | {fill:.0%} | {row['var']:.2f} | {row['range']:.2f} | {row['move']:.3f} | {row['board']:.3f} | "
              f"{row['stay']:.1%} | {row['bad2x']:.1%} | {row['occ']:+.2f} | {row['pick_random']:.1%}")
    for name, _, values in series:
        print(name, "各轮方差：", " ".join(f"{v:.2f}" for v in values[::5]))
