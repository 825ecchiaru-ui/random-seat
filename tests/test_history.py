#SeatAssignment.set_seat_assignment的正确性测试
#用法：python tests/test_history.py
import json
import random
from datetime import datetime

from layout_tools import m, example_layouts, grid_positions, build, snapshot
from test_weights import expect_error


def test_layouts():
    #在全部样例布局上，分别用满员、80%入座和单个学生连续排座多轮，每轮都记录方案
    for name, positions in example_layouts().items():
        n_seats = len(positions)
        for n in (n_seats, round(n_seats * 0.8), 1):
            random.seed(1)
            classroom, students, alloc = build(positions, n)

            for rnd in range(5):
                best, score = alloc.select_best_assignment()

                #记录方案：必须在apply_assignment之前，且不能修改学生和座位的状态
                before = snapshot(classroom, students)
                record = m.SeatAssignment(rnd)
                record.set_seat_assignment(best, score)
                assert snapshot(classroom, students) == before, "记录方案时状态被修改"

                assert record.assignment_id == rnd
                assert record.score == score and isinstance(record.score, float)

                #时间标记是精确到秒的ISO格式字符串，且就是刚才的时间
                assert isinstance(record.created_at, str) and len(record.created_at) == 19
                created = datetime.fromisoformat(record.created_at)
                assert abs((datetime.now() - created).total_seconds()) < 5

                #记录的学生恰好是方案中入座的全部学生
                assert set(record.assignments) == {stu.student_id for stu in best.values()}
                assert len(record.assignments) == n

                #每条记录只有两个键，座位坐标与方案一致
                for seat, stu in best.items():
                    entry = record.assignments[stu.student_id]
                    assert set(entry) == {"burden", "current_seat"}
                    assert entry["current_seat"] == [seat.x, seat.y]

                #空座位：方案字典里本来就没有它们，记录中也不应出现它们的坐标
                #记录到的坐标 + 空座位的坐标 应当恰好等于教室的全部座位
                recorded = {tuple(e["current_seat"]) for e in record.assignments.values()}
                empty = {(seat.x, seat.y) for seat in classroom.seats if seat not in best}
                assert len(recorded) == n and len(empty) == n_seats - n
                assert not (recorded & empty)
                assert recorded | empty == set(positions)

                #记录里不含Student和Seat对象，可以直接写成JSON
                #（整数的学生编号写入JSON后会变成字符串，这里把键转成字符串后比较）
                restored = json.loads(json.dumps(record.assignments))
                assert restored == {str(k): v for k, v in record.assignments.items()}

                #应用方案后：记录的burden等于学生实际的历史负担，坐标等于学生实际的座位
                #应用后空座位的student为None，这些座位都不在记录中
                alloc.apply_assignment(best)
                for stu in students:
                    entry = record.assignments[stu.student_id]
                    assert entry["burden"] == stu.accum_burden
                    assert entry["current_seat"] == [stu.current_seat.x, stu.current_seat.y]
                for seat in classroom.seats:
                    if seat.student is None:
                        assert (seat.x, seat.y) not in recorded
                    else:
                        assert record.assignments[seat.student.student_id]["current_seat"] == [seat.x, seat.y]

        print(f"布局测试通过  {name}（{n_seats}个座位）")


def test_edge_cases():
    #刚创建、还没有记录方案时的初始值
    record = m.SeatAssignment("a")
    assert (record.assignment_id, record.assignments, record.score, record.created_at) == ("a", {}, 0.0, None)

    #空方案（没有学生）：记录为空字典，分数和时间照常写入
    record.set_seat_assignment({}, 0)
    assert record.assignments == {} and record.created_at is not None
    assert str(record.score) == "0.0"

    #重复调用：整体覆盖上一次的记录，而不是合并
    classroom, students, alloc = build(grid_positions(3, 3), 5)
    first, first_score = alloc.select_best_assignment()
    record.set_seat_assignment(first, first_score)
    assert len(record.assignments) == 5
    second = {classroom.seats[0]: students[0]}
    record.set_seat_assignment(second, -1.5)
    assert record.assignments == {
        0: {"burden": classroom.seats[0].burden, "current_seat": [classroom.seats[0].x, classroom.seats[0].y]}
    }
    assert record.score == -1.5

    #学生编号重复：报错，且原有记录保持不变
    saved = (dict(record.assignments), record.score, record.created_at)
    duplicate = {classroom.seats[0]: m.Student("same"), classroom.seats[1]: m.Student("same")}
    expect_error(lambda: record.set_seat_assignment(duplicate, 0.0), "Duplicate student_id")
    assert (record.assignments, record.score, record.created_at) == saved

    #调用时机：在apply_assignment之后记录，新座位的负担会被重复计入（文档中规定必须先记录再应用）
    classroom, students, alloc = build(grid_positions(3, 3), 9)
    best, score = alloc.select_best_assignment()
    right = m.SeatAssignment(1)
    right.set_seat_assignment(best, score)
    alloc.apply_assignment(best)
    late = m.SeatAssignment(2)
    late.set_seat_assignment(best, score)
    for seat, stu in best.items():
        assert right.assignments[stu.student_id]["burden"] == stu.accum_burden
        assert late.assignments[stu.student_id]["burden"] == stu.accum_burden + seat.burden
    print("边界情况测试通过")


if __name__ == "__main__":
    test_layouts()
    test_edge_cases()
    print("全部通过")
