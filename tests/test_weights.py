#calculate_weights与generate_assignment的正确性测试
#用法：python tests/test_weights.py
import math
import random
import statistics

import numpy as np

from layout_tools import m, example_layouts, grid_positions, build, snapshot


def expected_weight_row(alloc, student):
    #不依赖numpy矩阵运算，按PRD公式逐个座位手算某个学生的一行权重，用于核对
    seats, students = alloc.classroom.seats, alloc.students
    board = [s.distance_to_board for s in seats]
    board_range = max(board) - min(board)
    max_move = max(math.dist((p.x, p.y), (q.x, q.y)) for p in seats for q in seats)
    mean_accum = statistics.fmean(s.accum_burden for s in students)
    ref_burden = statistics.fmean(sorted(s.burden for s in seats)[:len(students)])

    exponents = []
    cur = student.current_seat
    for seat in seats:
        b = abs(cur.distance_to_board - seat.distance_to_board) / board_range if cur and board_range else 0.0
        d = math.dist((cur.x, cur.y), (seat.x, seat.y)) / max_move if cur and max_move else 0.0
        h = (student.accum_burden - mean_accum) + (seat.burden - ref_burden)
        exponents.append(m.config.WEIGHT_BOARD * b + m.config.WEIGHT_MOVE * d + m.config.WEIGHT_HISTORY * -max(h, 0.0))
    top = max(exponents)
    return [math.exp(e - top) for e in exponents]


def check_assignment(assignment, classroom, students):
    #方案合法：全员有座、学生不重复、座位不重复且属于教室
    assert len(assignment) == len(students)
    assert set(assignment.values()) == set(students)
    assert set(assignment) <= set(classroom.seats)


def test_layouts():
    #在全部样例布局和一个矩形教室上，用不同的学生数量连续排座多轮
    layouts = dict(example_layouts())
    layouts["grid 6x8"] = grid_positions(6, 8)

    for name, positions in layouts.items():
        n_seats = len(positions)
        for n in (n_seats, round(n_seats * 0.85), round(n_seats * 0.5), 1):
            random.seed(n)
            classroom, students, alloc = build(positions, n)

            for rnd in range(12):      #第0轮为首次排座，全员没有当前座位
                before = snapshot(classroom, students)
                w = alloc.calculate_weights()

                #矩阵形状正确，权重落在(0,1]，每行最大值为1
                assert w.shape == (n, n_seats)
                assert np.all(np.isfinite(w)) and np.all(w >= 0) and np.all(w <= 1)
                assert np.allclose(w.max(axis=1), 1.0)

                #抽查三个学生的整行权重，与手算结果一致
                for i in {0, n // 2, n - 1}:
                    assert np.allclose(w[i], expected_weight_row(alloc, students[i]), rtol=1e-9, atol=1e-300)

                assignment = alloc.generate_assignment(w)
                assert snapshot(classroom, students) == before, "生成方案时状态被修改"
                check_assignment(assignment, classroom, students)

                alloc.apply_assignment(assignment)
                #应用后：占用座位数等于学生数，空座位无学生，学生与座位互相指向对方
                occupied = [s for s in classroom.seats if s.student is not None]
                assert len(occupied) == n
                assert all(s.student.current_seat is s for s in occupied)
                assert all(stu.current_seat.student is stu for stu in students)
                assert all(len(stu.seat_history) == rnd + 1 for stu in students)

            #累计负担等于历史上坐过的所有座位的负担之和
            burden_at = {(s.x, s.y): s.burden for s in classroom.seats}
            for stu in students:
                assert math.isclose(stu.accum_burden, sum(burden_at[p] for p in stu.seat_history), abs_tol=1e-9)

        print(f"布局测试通过  {name}（{n_seats}个座位）")


def test_priority_order():
    #抽取顺序：历史负担高者优先；相同时当前座位负担高者优先
    #构造一个每人都极度偏好座位0、其次座位1、再次座位2……的矩阵，
    #这样第k个抽取的学生几乎必然拿到座位k，从结果即可看出抽取顺序
    random.seed(0)
    classroom, students, alloc = build(grid_positions(2, 3), 4)
    alloc.apply_assignment(alloc.random_assignment())
    students[2].accum_burden = 10.0
    students[0].accum_burden = 5.0
    students[1].accum_burden = students[3].accum_burden = 1.0
    w = np.tile(np.array([1000.0 ** -k for k in range(6)]), (4, 1))

    first = students[2]
    second = students[0]
    #最后两名学生历史负担相同，由当前座位负担决定先后（负担恰好相同时无法区分，跳过该项）
    s1, s3 = students[1], students[3]
    third = None
    if s1.current_seat.burden != s3.current_seat.burden:
        third = s1 if s1.current_seat.burden > s3.current_seat.burden else s3

    hits = 0
    trials = 300
    for _ in range(trials):
        assignment = alloc.generate_assignment(w)
        seat_index = {stu: classroom.seats.index(seat) for seat, stu in assignment.items()}
        ok = seat_index[first] == 0 and seat_index[second] == 1
        if third is not None:
            ok = ok and seat_index[third] == 2
        hits += ok
    assert hits > trials * 0.95, hits
    print("抽取顺序测试通过")


def test_edge_cases():
    #单座位教室：B、D的分母为0
    classroom, students, alloc = build(grid_positions(1, 1), 1)
    for _ in range(3):
        w = alloc.calculate_weights()
        assert w.shape == (1, 1) and w[0, 0] == 1.0
        alloc.apply_assignment(alloc.generate_assignment(w))

    #没有学生
    classroom, students, alloc = build(grid_positions(2, 2), 0)
    assert alloc.calculate_weights().shape == (0, 4)
    assert alloc.generate_assignment(alloc.calculate_weights()) == {}

    #没有座位
    classroom, students, alloc = build(grid_positions(2, 2), 2)
    classroom.seats = []
    expect_error(alloc.calculate_weights, "Classroom has no seats")

    #学生多于座位
    classroom, students, alloc = build(grid_positions(3, 3), 10)
    expect_error(lambda: alloc.generate_assignment(alloc.calculate_weights()), "Not enough seats")

    #中途加入没有当前座位的学生（例如转学生）
    classroom, students, alloc = build(grid_positions(3, 3), 5)
    alloc.apply_assignment(alloc.random_assignment())
    students.append(m.Student("transfer"))
    assignment = alloc.generate_assignment(alloc.calculate_weights())
    check_assignment(assignment, classroom, students)

    #不合法的权重矩阵：形状不符、含负数、含NaN
    expect_error(lambda: alloc.generate_assignment(np.ones((2, 2))), "shape")
    expect_error(lambda: alloc.generate_assignment(-np.ones((6, 9))), "non-negative")
    expect_error(lambda: alloc.generate_assignment(np.full((6, 9), np.nan)), "non-negative")

    #权重为0的座位不会被抽中；某行剩余权重全为0时退化为等概率抽取
    w = np.zeros((6, 9))
    w[:, 8] = 1.0
    for _ in range(50):
        check_assignment(alloc.generate_assignment(w), classroom, students)
    w = np.zeros((6, 9))
    w[:, :6] = 1.0
    for _ in range(50):
        assignment = alloc.generate_assignment(w)
        assert {classroom.seats.index(seat) for seat in assignment} == set(range(6))

    #历史负担差距极大时，权重会小到被舍入为0，流程仍应正常
    students[0].accum_burden = 500.0
    w = alloc.calculate_weights()
    assert np.all(np.isfinite(w)) and np.allclose(w.max(axis=1), 1.0)
    check_assignment(alloc.generate_assignment(w), classroom, students)
    print("边界情况测试通过")


def expect_error(func, text):
    #确认func会抛出包含text的ValueError
    try:
        func()
    except ValueError as error:
        assert text in str(error), error
        return
    raise AssertionError(f"没有抛出预期的错误：{text}")


if __name__ == "__main__":
    test_layouts()
    test_priority_order()
    test_edge_cases()
    print("全部通过")
