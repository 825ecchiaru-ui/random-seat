#score_assignment与select_best_assignment的正确性测试
#用法：python tests/test_select.py
import random
import statistics
import time

from layout_tools import m, example_layouts, grid_positions, build, snapshot
from test_weights import expect_error


def test_layouts():
    #在全部样例布局上，分别用满员、80%入座和单个学生连续排座多轮
    for name, positions in example_layouts().items():
        n_seats = len(positions)
        for n in (n_seats, round(n_seats * 0.8), 1):
            random.seed(1)
            classroom, students, alloc = build(positions, n)

            for rnd in range(6):      #第0轮为首次排座，全员没有当前座位
                before = snapshot(classroom, students)
                random_state = random.getstate()
                best, score = alloc.select_best_assignment()

                assert snapshot(classroom, students) == before, "选优时状态被修改"
                assert isinstance(best, dict) and isinstance(score, float)
                assert len(best) == n and set(best.values()) == set(students)
                assert set(best) <= set(classroom.seats)

                #分数与手算一致：-(方差 + 极差)
                after = {stu: stu.accum_burden for stu in students}
                for seat, stu in best.items():
                    after[stu] += seat.burden
                values = list(after.values())
                expected = -(m.config.SCORE_WEIGHT_VAR * statistics.pvariance(values)
                             + m.config.SCORE_WEIGHT_RANGE * (max(values) - min(values)))
                assert abs(score - expected) < 1e-9
                assert abs(alloc.score_assignment(best) - expected) < 1e-9

                #恢复随机状态后按同样的顺序重新生成全部候选，确认返回的确实是最高分的方案
                random.setstate(random_state)
                w = alloc.calculate_weights()
                candidates = [alloc.generate_assignment(w) for _ in range(m.config.NUM_WEIGHTED_PLANS)]
                candidates += [alloc.random_assignment() for _ in range(m.config.NUM_RANDOM_PLANS)]
                scores = [alloc.score_assignment(c) for c in candidates]
                assert len(candidates) == m.config.NUM_WEIGHTED_PLANS + m.config.NUM_RANDOM_PLANS
                assert max(scores) == score
                assert candidates[scores.index(max(scores))] == best

                #应用后实际的方差和极差与分数吻合
                alloc.apply_assignment(best)
                acc = [s.accum_burden for s in students]
                assert abs(-(statistics.pvariance(acc) + max(acc) - min(acc)) - score) < 1e-9

        print(f"布局测试通过  {name}（{n_seats}个座位）")


def test_edge_cases():
    #没有学生：分数为0，方案为空
    classroom, students, alloc = build(grid_positions(2, 2), 0)
    assert alloc.score_assignment({}) == 0.0
    assert alloc.select_best_assignment() == ({}, 0.0)

    #全班负担完全相同时分数为0.0（不是-0.0）
    classroom, students, alloc = build(grid_positions(1, 1), 1)
    best, score = alloc.select_best_assignment()
    assert str(score) == "0.0"

    #学生多于座位
    classroom, students, alloc = build(grid_positions(2, 2), 5)
    expect_error(alloc.select_best_assignment, "Not enough seats")

    #候选数量：两类都为0时报错；只有纯随机候选时仍可选优
    classroom, students, alloc = build(grid_positions(3, 3), 5)
    saved = (m.config.NUM_WEIGHTED_PLANS, m.config.NUM_RANDOM_PLANS)
    try:
        m.config.NUM_WEIGHTED_PLANS, m.config.NUM_RANDOM_PLANS = 0, 0
        expect_error(alloc.select_best_assignment, "No candidate plans")
        m.config.NUM_WEIGHTED_PLANS, m.config.NUM_RANDOM_PLANS = 0, 4
        best, score = alloc.select_best_assignment()
        assert len(best) == 5
    finally:
        m.config.NUM_WEIGHTED_PLANS, m.config.NUM_RANDOM_PLANS = saved
    print("边界情况测试通过")


def report_time():
    #报告42人满员时每轮选优的耗时
    positions = example_layouts()["example.csv"]
    classroom, students, alloc = build(positions, len(positions))
    alloc.apply_assignment(alloc.select_best_assignment()[0])
    start = time.perf_counter()
    for _ in range(10):
        alloc.select_best_assignment()
    elapsed = (time.perf_counter() - start) / 10 * 1000
    print(f"每轮选优耗时约{elapsed:.1f}毫秒（{len(positions)}人满员，"
          f"{m.config.NUM_WEIGHTED_PLANS}个加权候选 + {m.config.NUM_RANDOM_PLANS}个纯随机候选）")


if __name__ == "__main__":
    test_layouts()
    test_edge_cases()
    report_time()
    print("全部通过")
