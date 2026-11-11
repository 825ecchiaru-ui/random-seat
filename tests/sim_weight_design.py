#比较不同权重设计（公式形式与系数）的模拟实验
#每种设计都走完整流程：50个加权候选 + 50个纯随机候选 -> 公平性打分 -> 应用最高分方案
#generate_assignment接收任意权重矩阵，因此这里可以在不改主程序的情况下试验各种权重公式
#用法：python tests/sim_weight_design.py [阶段名]
import sys
import math
import random
import statistics
from multiprocessing import Pool

import numpy as np

from layout_tools import m, example_layouts, build

ROUNDS = 20


def weight_terms(alloc, ref="all"):
    #计算三项目标的原始矩阵，形状均为(学生数, 座位数)
    #返回(B, D, h)：B、D已归一化到[0,1]；h为换座后相对全班平均的负担偏差（未做变换）
    #ref决定h中座位负担的参照值：
    #  "all"  -> 全教室座位负担的平均值（当前主程序）
    #  "best" -> 负担最低的N个座位的平均值（N为学生数）。有空座时引导学生坐满较好的座位，
    #            把最差的座位留空；满员时与"all"完全相同
    students, seats = alloc.students, alloc.classroom.seats
    seat_board = np.array([s.distance_to_board for s in seats])
    seat_x = np.array([s.x for s in seats], dtype=float)
    seat_y = np.array([s.y for s in seats], dtype=float)
    seat_burden = np.array([s.burden for s in seats])
    board_range = seat_board.max() - seat_board.min()
    max_move = np.hypot(seat_x[:, None] - seat_x[None, :], seat_y[:, None] - seat_y[None, :]).max()
    mean_accum = sum(s.accum_burden for s in students) / len(students)
    if ref == "best":
        ref_burden = np.sort(seat_burden)[:len(students)].mean()
    else:
        ref_burden = seat_burden.mean()

    b = np.zeros((len(students), len(seats)))
    d = np.zeros_like(b)
    h = np.zeros_like(b)
    for i, stu in enumerate(students):
        cur = stu.current_seat
        if cur is not None and board_range > 0:
            b[i] = np.abs(cur.distance_to_board - seat_board) / board_range
        if cur is not None and max_move > 0:
            d[i] = np.hypot(seat_x - cur.x, seat_y - cur.y) / max_move
        h[i] = (stu.accum_burden - mean_accum) + (seat_burden - ref_burden)
    return b, d, h


def make_matrix(alloc, design):
    #按设计参数生成权重矩阵
    #design = (form, h_form, a, b, c, sat)
    #  form：  "sum"  -> W = a*B + b*D + c*H            （当前主程序的线性加和）
    #          "exp"  -> W = exp(a*B + b*D + c*H)       （指数形式，系数同时控制偏好强度）
    #  h_form："inv"  -> H = 1/(1+|h|)                  （当前主程序）
    #          "lin"  -> H = -|h|                       （只能配合exp使用，等价于乘以exp(-c|h|)）
    #  sat：   B、D的饱和阈值。sat<1时B、D改为min(原值/sat, 1)，
    #          即“移动得足够远”后不再继续加分，避免偏向最远的外围座位
    #design可以带第7个元素ref（见weight_terms），省略时为"all"
    form, h_form, a, b_coef, c, sat = design[:6]
    b, d, h = weight_terms(alloc, design[6] if len(design) > 6 else "all")
    if sat < 1:
        b = np.minimum(b / sat, 1.0)
        d = np.minimum(d / sat, 1.0)
    #h>0表示换座后负担高于全班平均，h<0表示低于全班平均
    if h_form == "inv":
        h_term = 1 / (1 + np.abs(h))
    elif h_form == "lin":
        h_term = -np.abs(h)                    #高于、低于平均同等扣分
    elif h_form == "pos":
        h_term = -np.maximum(h, 0)             #只对高于平均扣分，低于平均不扣分
    else:
        #"asym"：高于平均按1倍扣分，低于平均按0.25倍扣分
        h_term = -(np.maximum(h, 0) + 0.25 * np.maximum(-h, 0))

    total = a * b + b_coef * d + c * h_term
    if form == "sum":
        return total
    #指数形式：每行先减去该行最大值再取指数，只改变整行的倍数、不改变抽取概率，避免数值溢出
    return np.exp(total - total.max(axis=1, keepdims=True))


def simulate(args):
    #对一种设计、一个布局、一种入座率、一个随机种子做一次多轮模拟，返回各项指标
    design, layout_name, positions, fill, n_weighted, n_random, seed = args
    n = max(1, round(len(positions) * fill))
    random.seed(seed)
    classroom, students, alloc = build(positions, n)
    seats = classroom.seats

    bd = [s.distance_to_board for s in seats]
    board_range = max(bd) - min(bd)
    max_move = max(math.dist((p.x, p.y), (q.x, q.y)) for p in seats for q in seats)
    #“差座位”：负担值位于全教室最高的四分之一
    bad_threshold = float(np.quantile([s.burden for s in seats], 0.75))

    picked_random = stay = bad_twice = pairs = 0
    d_vals, b_vals, occ_burden = [], [], []
    for rnd in range(ROUNDS + 1):
        cands = []
        if n_weighted:
            #design为"main"时直接使用主程序的calculate_weights，其余情况使用本文件的试验公式
            w = alloc.calculate_weights() if design == "main" else make_matrix(alloc, design)
            cands += [alloc.generate_assignment(w) for _ in range(n_weighted)]
        cands += [alloc.random_assignment() for _ in range(n_random)]
        scores = [alloc.score_assignment(c) for c in cands]
        k = scores.index(max(scores))
        best = cands[k]

        if rnd > 0:      #第0轮是首次排座，没有“上一个座位”，不计入满意度指标
            picked_random += k >= n_weighted
            for seat, stu in best.items():
                old = stu.current_seat
                d_vals.append(math.dist((old.x, old.y), (seat.x, seat.y)) / max_move)
                b_vals.append(abs(old.distance_to_board - seat.distance_to_board) / board_range)
                stay += old is seat
                bad_twice += old.burden >= bad_threshold and seat.burden >= bad_threshold
                pairs += 1
            occ_burden.append(statistics.fmean(seat.burden for seat in best))
        alloc.apply_assignment(best)

    acc = [s.accum_burden for s in students]
    return (design, fill, n_weighted, n_random,
            statistics.pvariance(acc), max(acc) - min(acc),
            statistics.fmean(d_vals), statistics.fmean(b_vals),
            stay / pairs, bad_twice / pairs, statistics.fmean(occ_burden),
            picked_random / ROUNDS)


METRICS = ("var", "range", "move", "board", "stay", "bad2x", "occBurd", "pickRand")


def run(designs, fills, seeds, n_weighted=50, n_random=50):
    layouts = example_layouts()
    jobs = [(dsg, name, pos, fill, n_weighted, n_random, seed)
            for dsg in designs for name, pos in layouts.items() for fill in fills for seed in range(seeds)]
    with Pool(6) as pool:
        results = pool.map(simulate, jobs, chunksize=4)

    for fill in fills:
        print(f"\n=== fill {fill:.0%}, {n_weighted} weighted + {n_random} random, "
              f"{len(layouts)} layouts x {seeds} seeds ===")
        print(f"{'design (form, H, a, b, c, sat)':38s}" + " ".join(f"{x:>8s}" for x in METRICS))
        for dsg in designs:
            sel = [r for r in results if r[0] == dsg and r[1] == fill]
            print(f"{str(dsg):38s}" + " ".join(f"{statistics.fmean(r[4 + i] for r in sel):8.3f}"
                                               for i in range(len(METRICS))))
    return results


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "coarse"

    if stage == "baseline":
        #基准：当前设计、只用纯随机候选（100个）
        run([("sum", "inv", 1, 1, 1, 1)], (1.0, 0.85), 16)
        run([("sum", "inv", 1, 1, 1, 1)], (1.0, 0.85), 16, n_weighted=0, n_random=100)

    if stage == "coarse":
        designs = [("sum", "inv", 1, 1, 1, 1), ("sum", "inv", 1, 1, 5, 1), ("sum", "inv", 0, 0, 1, 1)]
        for h_form in ("inv", "lin"):
            for ab in (0, 1, 2, 4):
                for c in (2, 4, 8, 16):
                    designs.append(("exp", h_form, ab, ab, c, 1))
        run(designs, (1.0, 0.85), 6)

    if stage == "fine":
        #细筛：指数形式 + H=-|h|，比较更大的系数和两种座位负担参照值
        designs = [("sum", "inv", 1, 1, 1, 1, "all")]
        for ref in ("all", "best"):
            for ab, c in ((1, 4), (2, 8), (4, 8), (4, 16), (8, 16), (8, 32)):
                designs.append(("exp", "lin", ab, ab, c, 1, ref))
        run(designs, (1.0, 0.85, 0.7), 12)

    if stage == "hform":
        #比较H对“高于平均”和“低于平均”是否对称扣分，目的是让有空座时空出来的是差座位
        designs = [("sum", "inv", 1, 1, 1, 1, "all")]
        for h_form in ("lin", "asym", "pos"):
            for ref in ("all", "best"):
                for ab, c in ((2, 8), (4, 8)):
                    designs.append(("exp", h_form, ab, ab, c, 1, ref))
        run(designs, (1.0, 0.85, 0.7), 12)
    if stage == "final":
        #确定系数：指数形式 + 单侧扣分的H + 以最好的N个座位为参照
        designs = [("sum", "inv", 1, 1, 1, 1, "all")]
        for ab, c in ((2, 8), (4, 4), (4, 8), (4, 16), (8, 8), (8, 16)):
            designs.append(("exp", "pos", ab, ab, c, 1, "best"))
        run(designs, (1.0, 0.85, 0.7), 12)

    if stage == "hform":
        #参照：只用纯随机候选时已坐座位的平均负担
        run([("sum", "inv", 1, 1, 1, 1, "all")], (0.85, 0.7), 12, n_weighted=0, n_random=100)
