#HistoryManager（SQLite历史记录）与SeatAllocator.restore_state的正确性测试
#用法：python tests/test_history_manager.py
#测试用的数据库文件都建在系统临时目录中，测试结束后随临时目录一起清除，不会在项目里留下文件
import math
import random
import sqlite3
import statistics
import tempfile
from pathlib import Path

from layout_tools import m, example_layouts, grid_positions
from test_weights import expect_error


def build_class(history, class_name, positions, n_students):
    #在数据库中建立一个班级，并按数据库中的内容搭建教室，返回(classroom, students, allocator)
    #学生编号用字符串（数据库中student_id一律是字符串）
    history.add_class(class_name)
    history.set_seats(class_name, positions)
    for i in range(n_students):
        history.add_student(class_name, f"{class_name}-{i}")
    return load_class(history, class_name)


def load_class(history, class_name):
    #只凭数据库中的内容搭建教室并恢复状态，即程序重新启动后的流程
    return m.load_class(history, class_name)


def run_round(history, class_name, alloc):
    #完整的一轮排座：选优 -> 记录到对象 -> 应用 -> 存入数据库，返回记录对象
    best, score = alloc.select_best_assignment()
    record = m.SeatAssignment(None)
    record.set_seat_assignment(best, score)
    alloc.apply_assignment(best)
    history.record_assignment(class_name, record)
    return record


def plain_state(classroom, students):
    #把学生和座位的全部状态转成不含对象的形式，用于比较两套对象的状态是否相同
    return (
        [(s.student_id, None if s.current_seat is None else (s.current_seat.x, s.current_seat.y),
          list(s.seat_history), s.accum_burden) for s in students],
        [(seat.x, seat.y, None if seat.student is None else seat.student.student_id) for seat in classroom.seats],
    )


def test_files(folder):
    #文件不存在：新建数据库，五张表齐全，各项内容为空
    path = folder / "new.db"
    history = m.HistoryManager(path)
    assert path.exists() and history.get_classes() == [] and history.check_integrity() == []
    history.add_class("A")
    history.close()

    #再次打开：内容还在
    history = m.HistoryManager(path)
    assert history.get_classes() == ["A"]
    history.close()

    #空文件：当作新数据库
    empty = folder / "empty.db"
    empty.write_bytes(b"")
    history = m.HistoryManager(empty)
    assert history.get_classes() == []
    history.close()

    #所在文件夹不存在
    expect_error(lambda: m.HistoryManager(folder / "no_such_folder" / "a.db"), "Directory does not exist")

    #不是SQLite数据库的文件（格式错误），报错后文件内容不变
    garbage = folder / "garbage.db"
    garbage.write_text("这不是数据库" * 100, encoding="utf-8")
    expect_error(lambda: m.HistoryManager(garbage), "Not a valid SQLite database")
    assert garbage.read_text(encoding="utf-8") == "这不是数据库" * 100

    #是SQLite数据库，但不是本程序的（表结构不同），报错后不会往里面建表
    other = folder / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE classes (id INTEGER PRIMARY KEY, title TEXT)")
    conn.commit()
    conn.close()
    expect_error(lambda: m.HistoryManager(other), "Not a seat history database")
    conn = sqlite3.connect(other)
    assert [row[0] for row in conn.execute("SELECT name FROM sqlite_master")] == ["classes"]
    conn.close()
    print("文件处理测试通过")


def test_restore(folder):
    #多个班级（不同布局，满员与非满员）交替排座多轮，每轮都与数据库中的内容核对；
    #然后关闭数据库、重新打开，只凭数据库重建状态，应与内存中的状态完全相同
    path = folder / "restore.db"
    history = m.HistoryManager(path)
    layouts = list(example_layouts().items())
    plans = {"满员班": (layouts[0][1], len(layouts[0][1])),
             "非满员班": (layouts[1][1], round(len(layouts[1][1]) * 0.8)),
             "矩形班": (grid_positions(4, 5), 17)}

    random.seed(3)
    live = {name: build_class(history, name, positions, n) for name, (positions, n) in plans.items()}
    assert history.get_classes() == list(plans)

    #还没有任何方案时：全员没有座位，历史负担为0
    for name, (classroom, students, alloc) in live.items():
        assert history.get_seats(name) == plans[name][0]
        assert all(s.current_seat is None and s.seat_history == [] and s.accum_burden == 0.0 for s in students)

    states = {name: [plain_state(*live[name][:2])] for name in plans}   #每个班每一轮之后的状态
    records = {name: [] for name in plans}
    for rnd in range(6):
        for name, (classroom, students, alloc) in live.items():
            record = run_round(history, name, alloc)
            records[name].append(record)
            states[name].append(plain_state(classroom, students))

            #数据库分配的编号写回了记录对象，且全局递增
            assert isinstance(record.assignment_id, int)

            #load_state读出的内容与内存中的学生状态逐项相同（历史负担要求完全相等，不允许误差）
            state = history.load_state(name)
            assert list(state) == [s.student_id for s in students]
            for s in students:
                info = state[s.student_id]
                assert info["seat_history"] == s.seat_history
                assert info["accum_burden"] == s.accum_burden
                assert info["current_seat"] == (s.current_seat.x, s.current_seat.y)

    all_ids = [r.assignment_id for rnd in range(6) for name in plans for r in [records[name][rnd]]]
    assert all_ids == sorted(all_ids) and len(set(all_ids)) == 18
    assert history.check_integrity() == []
    history.close()

    #重新启动：重新打开数据库，只凭其中的内容重建，状态与关闭前完全相同
    history = m.HistoryManager(path)
    for name, (classroom, students, alloc) in live.items():
        classroom2, students2, alloc2 = load_class(history, name)
        assert plain_state(classroom2, students2) == plain_state(classroom, students)
        #恢复出的对象之间的引用关系正确
        for s in students2:
            assert s.current_seat.student is s

        #恢复后的行为也相同：用同样的随机种子，新旧两套对象选出同一个方案、同样的分数
        random.seed(11)
        weights = alloc.calculate_weights()
        best, score = alloc.select_best_assignment()
        random.seed(11)
        weights2 = alloc2.calculate_weights()
        best2, score2 = alloc2.select_best_assignment()
        assert weights.tobytes() == weights2.tobytes() and score == score2
        assert ({(seat.x, seat.y): stu.student_id for seat, stu in best.items()}
                == {(seat.x, seat.y): stu.student_id for seat, stu in best2.items()})

    #回退：逐次删除最近一次方案，每次恢复出的状态都等于当时记下的状态
    name = "非满员班"
    classroom, students, alloc = live[name]
    other_before = history.load_state("满员班")
    for rnd in range(6, 0, -1):
        assert history.rollback_last(name) == records[name][rnd - 1].assignment_id
        alloc.restore_state(history.load_state(name))
        assert plain_state(classroom, students) == states[name][rnd - 1]
    assert history.query_assignments(class_name=name) == []
    expect_error(lambda: history.rollback_last(name), "No assignment to roll back")
    #回退一个班不影响别的班
    assert history.load_state("满员班") == other_before
    assert len(history.query_assignments()) == 12

    #回退后继续排座：新方案的编号大于此前出现过的所有编号（被删除的编号不会重新使用）
    record = run_round(history, name, alloc)
    assert record.assignment_id > max(all_ids)
    assert history.check_integrity() == []
    history.close()
    print("恢复与回退测试通过")


def test_queries(folder):
    #按班级、学生、时间、方案编号查询
    history = m.HistoryManager(folder / "query.db")
    random.seed(5)
    classes = {"甲班": build_class(history, "甲班", grid_positions(3, 3), 6),
               "乙班": build_class(history, "乙班", grid_positions(2, 4), 7)}
    #两个班里各有一个student_id相同的学生
    history.add_student("甲班", "同名")
    history.add_student("乙班", "同名")
    classes = {name: load_class(history, name) for name in classes}

    #每个班排3轮。为了测试时间查询，这里把记录的时间改成指定的日期后再存入
    #（正常使用时created_at由set_seat_assignment写入，不需要改）
    records = {}
    for day in (1, 2, 3):
        for name, (classroom, students, alloc) in classes.items():
            best, score = alloc.select_best_assignment()
            record = m.SeatAssignment(None)
            record.set_seat_assignment(best, score)
            record.created_at = f"2026-11-0{day}T08:00:00"
            alloc.apply_assignment(best)
            history.record_assignment(name, record)
            records[(name, day)] = record

    #不加条件：全部6个方案，按编号排列；内容与当时的记录对象完全一致
    everything = history.query_assignments()
    assert [r["assignment_id"] for r in everything] == sorted(r.assignment_id for r in records.values())
    for (name, day), record in records.items():
        found = history.query_assignments(assignment_id=record.assignment_id)
        assert found == [{"assignment_id": record.assignment_id, "class_name": name, "score": record.score,
                          "created_at": record.created_at, "assignments": record.assignments}]

    #按班级
    assert [r["class_name"] for r in history.query_assignments(class_name="乙班")] == ["乙班"] * 3
    #按时间：含两端；只给一端；end只写日期时不包含当天
    assert len(history.query_assignments(start="2026-11-02T08:00:00", end="2026-11-03T08:00:00")) == 4
    assert len(history.query_assignments(start="2026-11-03")) == 2
    assert len(history.query_assignments(end="2026-11-02")) == 2
    assert len(history.query_assignments(end="2026-11-02T23:59:59")) == 4
    #按学生：只属于甲班的学生；两个班都有的student_id；再加班级条件
    assert [r["class_name"] for r in history.query_assignments(student_id="甲班-0")] == ["甲班"] * 3
    assert len(history.query_assignments(student_id="同名")) == 6
    assert len(history.query_assignments(student_id="同名", class_name="甲班")) == 3
    #多个条件同时满足
    assert len(history.query_assignments(class_name="甲班", start="2026-11-02", student_id="甲班-0")) == 2
    #查不到时返回空列表；班级不存在时报错
    assert history.query_assignments(assignment_id=9999) == []
    assert history.query_assignments(student_id="没有这个人") == []
    expect_error(lambda: history.query_assignments(class_name="丙班"), "Unknown class")

    #单个学生的历史：与学生对象中的seat_history、accum_burden一致
    classroom, students, alloc = classes["甲班"]
    for s in students:
        track = history.get_student_history("甲班", s.student_id)
        assert [t["seat"] for t in track] == s.seat_history
        assert track[-1]["burden"] == s.accum_burden
        assert [t["created_at"] for t in track] == [f"2026-11-0{day}T08:00:00" for day in (1, 2, 3)]
    expect_error(lambda: history.get_student_history("甲班", "乙班-0"), "Unknown student")
    history.close()
    print("查询测试通过")


def test_roster_and_layout(folder):
    #学生离开、回来，以及更换座位表
    history = m.HistoryManager(folder / "roster.db")
    random.seed(7)
    classroom, students, alloc = build_class(history, "A", grid_positions(3, 3), 5)
    for _ in range(3):
        run_round(history, "A", alloc)
    leaver = students[0]
    track = history.get_student_history("A", leaver.student_id)
    assert len(track) == 3

    #学生离开：不再出现在名单和状态中，但历史保留，仍可查询
    history.set_student_active("A", leaver.student_id, False)
    assert leaver.student_id not in history.get_students("A")
    assert leaver.student_id in history.get_students("A", active_only=False)
    assert leaver.student_id not in history.load_state("A")
    assert history.get_student_history("A", leaver.student_id) == track
    assert len(history.query_assignments(student_id=leaver.student_id)) == 3

    #方案中含有已离开的学生时不能保存，且数据库内容不变
    best, score = alloc.select_best_assignment()
    record = m.SeatAssignment(None)
    record.set_seat_assignment(best, score)
    expect_error(lambda: history.record_assignment("A", record), "Student not in class or inactive")
    assert len(history.query_assignments()) == 3 and record.assignment_id is None

    #其余学生继续排座2轮；离开的学生回来后：历史负担从其余在班学生当前的平均值开始
    #（不是离开前的值），座位历史保留，没有当前座位
    classroom, students, alloc = load_class(history, "A")
    assert len(students) == 4
    for _ in range(2):
        run_round(history, "A", alloc)
    others_mean = statistics.mean(s.accum_burden for s in students)
    assert abs(others_mean - track[-1]["burden"]) > 1e-6        #两个值确实不同，下面的断言才有意义
    history.set_student_active("A", leaver.student_id, True)
    state = history.load_state("A")
    assert list(state) == history.get_students("A") and len(state) == 5
    returned = state[leaver.student_id]
    assert returned["seat_history"] == [t["seat"] for t in track] and returned["current_seat"] is None
    assert abs(returned["accum_burden"] - others_mean) < 1e-12
    #其余学生不受影响
    for s in students:
        assert state[s.student_id]["accum_burden"] == s.accum_burden
    #已经在班的学生再次标记为在班：什么都不变
    history.set_student_active("A", leaver.student_id, True)
    assert history.load_state("A") == state
    classroom, students, alloc = load_class(history, "A")
    assert sum(seat.student is not None for seat in classroom.seats) == 4

    #回来后排一轮：新的负担 = 起点 + 新座位的负担；回退这一轮后又回到起点
    record = run_round(history, "A", alloc)
    back = next(s for s in students if s.student_id == leaver.student_id)
    assert abs(back.accum_burden - (returned["accum_burden"] + back.current_seat.burden)) < 1e-12
    assert history.load_state("A")[leaver.student_id]["accum_burden"] == back.accum_burden
    assert history.rollback_last("A") == record.assignment_id
    assert history.load_state("A") == state

    #中途加入的新学生：同样从其余在班学生当前的平均值开始，从未入座，没有座位历史
    everyone_mean = statistics.mean(info["accum_burden"] for info in state.values())
    history.add_student("A", "新同学")
    newcomer = history.load_state("A")["新同学"]
    assert newcomer["seat_history"] == [] and newcomer["current_seat"] is None
    assert abs(newcomer["accum_burden"] - everyone_mean) < 1e-12
    #再加一个：平均值不变，所以起点相同
    history.add_student("A", "新同学2")
    assert abs(history.load_state("A")["新同学2"]["accum_burden"] - everyone_mean) < 1e-12
    #新学生离开再回来：起点重新按当时的平均值计算（此时仍然相同）；从未入座，可以删除
    history.set_student_active("A", "新同学2", False)
    history.set_student_active("A", "新同学2", True)
    assert abs(history.load_state("A")["新同学2"]["accum_burden"] - everyone_mean) < 1e-12
    history.delete_student("A", "新同学2")
    history.delete_student("A", "新同学")
    assert history.load_state("A") == state

    #更换座位表：历史记录不变；原座位已不在新座位表中的学生没有当前座位，其余学生保留
    before = history.query_assignments()
    old_state = history.load_state("A")
    new_positions = [(x, y) for x, y in grid_positions(3, 3) if x != 1] + [(4, 0), (4, 1)]
    history.set_seats("A", new_positions)
    assert history.get_seats("A") == new_positions
    assert history.query_assignments() == before
    new_state = history.load_state("A")
    for sid, info in new_state.items():
        old = old_state[sid]
        assert info["seat_history"] == old["seat_history"] and info["accum_burden"] == old["accum_burden"]
        expected = old["current_seat"] if old["current_seat"] in new_positions else None
        assert info["current_seat"] == expected
    #在新座位表上可以正常恢复并继续排座
    classroom, students, alloc = load_class(history, "A")
    run_round(history, "A", alloc)
    assert history.check_integrity() == []
    history.close()
    print("名单与座位表变动测试通过")


def test_errors_and_integrity(folder):
    path = folder / "errors.db"
    history = m.HistoryManager(path)
    classroom, students, alloc = build_class(history, "A", grid_positions(2, 2), 3)
    build_class(history, "B", grid_positions(2, 2), 2)

    #管理类方法的输入错误
    expect_error(lambda: history.add_class("A"), "Class already exists")
    expect_error(lambda: history.add_student("A", "A-0"), "Student already exists")
    expect_error(lambda: history.add_student("C", "x"), "Unknown class")
    expect_error(lambda: history.set_seats("A", [(1, 1), (1, 1)]), "Duplicate seat positions")
    assert history.get_seats("A") == grid_positions(2, 2)      #报错后座位表不变
    expect_error(lambda: history.set_student_active("A", "nobody", False), "Unknown student")
    expect_error(lambda: history.load_state("C"), "Unknown class")

    #保存方案时的各种错误，每次报错后数据库中都不应多出记录
    best, score = alloc.select_best_assignment()
    record = m.SeatAssignment(None)
    expect_error(lambda: history.record_assignment("A", record), "Record has no assignment set")
    record.set_seat_assignment(best, score)
    expect_error(lambda: history.record_assignment("C", record), "Unknown class")
    expect_error(lambda: history.record_assignment("B", record), "Student not in class or inactive")
    history.set_seats("A", [(9, 9), (9, 8), (9, 7)])
    expect_error(lambda: history.record_assignment("A", record), "Seat not in class layout")
    history.set_seats("A", grid_positions(2, 2))
    #两个学生的记录指向同一个座位（直接拼出一份错误的内容）
    bad = m.SeatAssignment(None)
    bad.set_seat_assignment(best, score)
    first, second = list(bad.assignments)[:2]
    bad.assignments[second]["current_seat"] = list(bad.assignments[first]["current_seat"])
    expect_error(lambda: history.record_assignment("A", bad), "A seat cannot hold multiple students")
    assert history.query_assignments() == []

    #正常保存后数据完整
    alloc.apply_assignment(best)
    history.record_assignment("A", record)
    assert history.check_integrity() == []

    #restore_state的输入检查，报错时状态保持不变
    state = history.load_state("A")
    before = plain_state(classroom, students)
    missing = {sid: info for sid, info in state.items() if sid != "A-0"}
    expect_error(lambda: alloc.restore_state(missing), "State does not match students")
    extra = dict(state, 多出来的人={"seat_history": [], "accum_burden": 0.0, "current_seat": None})
    expect_error(lambda: alloc.restore_state(extra), "State does not match students")
    invalid = {sid: dict(info) for sid, info in state.items()}
    invalid["A-0"]["current_seat"] = (9, 9)
    expect_error(lambda: alloc.restore_state(invalid), "State contains invalid seats")
    doubled = {sid: dict(info) for sid, info in state.items()}
    doubled["A-0"]["current_seat"] = doubled["A-1"]["current_seat"]
    expect_error(lambda: alloc.restore_state(doubled), "A seat cannot hold multiple students")
    assert plain_state(classroom, students) == before
    #student_id是整数的学生与数据库中的字符串对不上，会被检查出来
    int_alloc = m.SeatAllocator(classroom, [m.Student(i) for i in range(3)])
    expect_error(lambda: int_alloc.restore_state(state), "State does not match students")
    history.close()

    #完整性检查：绕过HistoryManager直接改坏数据库，检查应当能发现
    conn = sqlite3.connect(path)       #这个连接没有开启外键检查，所以能写入错误的数据
    assignment_id = conn.execute("SELECT id FROM assignments").fetchone()[0]
    b_student = conn.execute("SELECT s.id FROM students s JOIN classes c ON c.id = s.class_id"
                             " WHERE c.name = 'B'").fetchone()[0]
    conn.execute("INSERT INTO assignment_entries VALUES (?, ?, 5, 5, 0.0)", (assignment_id, b_student))
    conn.execute("INSERT INTO assignment_entries VALUES (?, 9999, 6, 6, 0.0)", (assignment_id,))
    conn.commit()
    conn.close()
    history = m.HistoryManager(path)
    problems = history.check_integrity()
    assert len(problems) == 2, problems
    assert any("another class" in p for p in problems) and any("missing row of students" in p for p in problems)
    history.close()
    print("错误处理与完整性检查测试通过")


def test_management(folder):
    #with写法、最近N条查询、完整名单、改名和删除
    path = folder / "manage.db"

    #with写法：离开with块后数据库已关闭（再使用会报错），块内出错时同样关闭且错误照常抛出
    with m.HistoryManager(path) as history:
        history.add_class("甲")
    try:
        history.get_classes()
        raise AssertionError("数据库没有被关闭")
    except sqlite3.ProgrammingError:
        pass
    try:
        with m.HistoryManager(path) as history:
            history.add_class("甲")
        raise AssertionError("没有抛出预期的错误")
    except ValueError as error:
        assert "Class already exists" in str(error)
    try:
        history.get_classes()
        raise AssertionError("数据库没有被关闭")
    except sqlite3.ProgrammingError:
        pass

    with m.HistoryManager(path) as history:
        random.seed(9)
        #空班级（没有座位表、没有学生）也可以正常载入
        classroom, students, alloc = m.load_class(history, "甲")
        assert classroom.seats == [] and students == []
        expect_error(lambda: m.load_class(history, "没有这个班"), "Unknown class")

        history.set_seats("甲", grid_positions(3, 3))
        for name in ("a", "b", "c", "d"):
            history.add_student("甲", name)
        classroom, students, alloc = build_class(history, "乙", grid_positions(2, 3), 4)
        classroom, students, alloc = load_class(history, "甲")
        ids = [run_round(history, "甲", alloc).assignment_id for _ in range(5)]
        other_classroom, other_students, other_alloc = load_class(history, "乙")
        other_ids = [run_round(history, "乙", other_alloc).assignment_id for _ in range(2)]

        #limit：取最近的N个，仍按时间先后排列；可与其他条件组合
        everything = history.query_assignments(class_name="甲")
        assert [r["assignment_id"] for r in everything] == ids
        assert history.query_assignments(class_name="甲", limit=1) == everything[-1:]
        assert history.query_assignments(class_name="甲", limit=3) == everything[-3:]
        assert history.query_assignments(class_name="甲", limit=99) == everything
        assert history.query_assignments(class_name="甲", limit=0) == []
        assert [r["assignment_id"] for r in history.query_assignments(limit=3)] == [ids[-1]] + other_ids
        assert history.query_assignments(class_name="甲", student_id="a", limit=2) == everything[-2:]
        expect_error(lambda: history.query_assignments(limit=-1), "limit must be a non-negative integer")
        expect_error(lambda: history.query_assignments(limit="3"), "limit must be a non-negative integer")
        #极大的limit等同于不限数量，不会出错
        assert history.query_assignments(class_name="甲", limit=10 ** 30) == everything
        assert history.latest_assignment_id("甲") == ids[-1] and history.latest_assignment_id("乙") == other_ids[-1]

        #完整名单：包含已离开的学生，带在班状态
        history.set_student_active("甲", "b", False)
        assert history.get_roster("甲") == [("a", True), ("b", False), ("c", True), ("d", True)]
        expect_error(lambda: history.get_roster("没有这个班"), "Unknown class")

        #学生改名：历史跟着走，名单顺序不变
        track = history.get_student_history("甲", "a")
        history.rename_student("甲", "a", "阿明")
        assert history.get_roster("甲")[0] == ("阿明", True)
        assert history.get_student_history("甲", "阿明") == track
        assert "阿明" in history.query_assignments(class_name="甲", limit=1)[0]["assignments"]
        expect_error(lambda: history.get_student_history("甲", "a"), "Unknown student")
        expect_error(lambda: history.rename_student("甲", "阿明", "c"), "Student already exists")
        expect_error(lambda: history.rename_student("甲", "nobody", "x"), "Unknown student")
        #别的班有同名学生不受影响
        history.rename_student("甲", "c", "乙-0")
        assert "乙-0" in history.get_students("乙") and "乙-0" in history.get_students("甲")

        #删除学生：只有从未入座的学生可以删除
        history.add_student("甲", "录错的")
        history.delete_student("甲", "录错的")
        assert "录错的" not in [sid for sid, active in history.get_roster("甲")]
        expect_error(lambda: history.delete_student("甲", "阿明"), "Student has history")
        expect_error(lambda: history.delete_student("甲", "录错的"), "Unknown student")
        assert len(history.get_roster("甲")) == 4

        #班级改名：座位表、名单、历史和状态都保留
        state = history.load_state("甲")
        history.rename_class("甲", "高一1班")
        assert history.get_classes() == ["高一1班", "乙"]
        assert history.load_state("高一1班") == state
        assert [r["assignment_id"] for r in history.query_assignments(class_name="高一1班")] == ids
        assert {r["class_name"] for r in history.query_assignments(class_name="高一1班")} == {"高一1班"}
        expect_error(lambda: history.rename_class("甲", "丙"), "Unknown class")
        expect_error(lambda: history.rename_class("高一1班", "乙"), "Class already exists")

        #删除班级：它的座位表、名单、历史全部消失；别的班不受影响；数据保持完整
        other_state = history.load_state("乙")
        history.delete_class("高一1班")
        assert history.get_classes() == ["乙"]
        assert [r["assignment_id"] for r in history.query_assignments()] == other_ids
        assert history.load_state("乙") == other_state
        assert history.check_integrity() == []
        #各表中只剩下乙班的数据
        for table in ("seats", "students", "assignments"):
            assert len({row[0] for row in history.conn.execute(f"SELECT class_id FROM {table}")}) == 1, table
        assert {row[0] for row in history.conn.execute("SELECT assignment_id FROM assignment_entries")} == set(other_ids)
        expect_error(lambda: history.delete_class("高一1班"), "Unknown class")
        #删除后可以用同一个名字重新建立，是一个全新的空班级
        history.add_class("高一1班")
        assert history.get_seats("高一1班") == [] and history.get_roster("高一1班") == []
        #新方案的编号不会重新使用被删除班级用过的编号
        assert run_round(history, "乙", load_class(history, "乙")[2]).assignment_id > max(ids + other_ids)
    print("管理功能测试通过")


#版本1（最初）的建表语句，用来造一个旧版本的数据库文件，测试自动升级
V1_SCHEMA = """
CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE seats (class_id INTEGER NOT NULL REFERENCES classes(id), x INTEGER NOT NULL, y INTEGER NOT NULL,
    PRIMARY KEY (class_id, x, y));
CREATE TABLE students (id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL REFERENCES classes(id),
    student_id TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE (class_id, student_id));
CREATE TABLE assignments (id INTEGER PRIMARY KEY AUTOINCREMENT, class_id INTEGER NOT NULL REFERENCES classes(id),
    score REAL NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE assignment_entries (assignment_id INTEGER NOT NULL REFERENCES assignments(id),
    student INTEGER NOT NULL REFERENCES students(id), x INTEGER NOT NULL, y INTEGER NOT NULL, burden REAL NOT NULL,
    PRIMARY KEY (assignment_id, student), UNIQUE (assignment_id, x, y));
"""


def test_names_and_limits(folder):
    #名称的检查与整理、坐标范围、数量上限
    with m.HistoryManager(folder / "limits.db") as history:
        #名称整理：首尾空格去掉，全角空格和连续空格统一成一个半角空格；返回整理后的名称
        assert history.add_class("  高一　 3班 ") == "高一 3班"
        assert history.get_classes() == ["高一 3班"]
        #整理后相同的名称算重名
        expect_error(lambda: history.add_class("高一 3班"), "Class already exists")
        expect_error(lambda: history.add_class("高一　3班"), "Class already exists")
        #不合格的名称
        bad_names = [("", "must not be empty"), ("  　 ", "must not be empty"), ("a/b", "must not contain"),
                     (".", "must not contain"), ("..", "must not contain"), ("x" * 51, "too long"),
                     ("甲\n乙", "invisible or control"), ("甲\t乙", "invisible or control"),
                     ("甲​乙", "invisible or control"), ("﻿甲", "invisible or control"),
                     (123, "must be text"), (None, "must be text")]
        for name, text in bad_names:
            expect_error(lambda: history.add_class(name), text)
            expect_error(lambda: history.rename_class("高一 3班", name), text)
        assert history.get_classes() == ["高一 3班"]
        assert history.add_class("x" * 50) == "x" * 50            #恰好50个字可以
        assert history.add_class("...") == "..." and history.add_class("a.b") == "a.b"   #只有单独的.和..不行
        #改成自己现在的名字（或只差空格）不算重名
        assert history.rename_class("高一 3班", " 高一  3班") == "高一 3班"
        assert history.rename_class("高一 3班", "A") == "A"

        #学生编号同样整理和检查；数字会转成文字
        assert history.add_student("A", " 张　三 ") == "张 三"
        assert history.add_student("A", 7) == "7"
        expect_error(lambda: history.add_student("A", "张  三"), "Student already exists")
        expect_error(lambda: history.add_student("A", "7"), "Student already exists")
        for name, text in bad_names[:-2]:
            expect_error(lambda: history.add_student("A", name), text)
            expect_error(lambda: history.rename_student("A", "7", name), text)
        assert history.rename_student("A", "7", " 李　四") == "李 四"
        assert history.rename_student("A", "李 四", "李  四") == "李 四"      #改成自己：不算重名
        expect_error(lambda: history.rename_student("A", "李 四", "张　三"), "Student already exists")
        assert history.get_students("A") == ["张 三", "李 四"]

        #座位坐标：x必须在1到100之间（x=0是黑板），y在0到100之间，都必须是整数
        good = [(1, 0), (100, 100), (50, 7)]
        history.set_seats("A", good)
        bad_layouts = [([(0, 1)], "out of range"), ([(-1, 1)], "out of range"), ([(1, -1)], "out of range"),
                       ([(101, 1)], "out of range"), ([(1, 101)], "out of range"), ([(1, 10 ** 9)], "out of range"),
                       ([(1, 10 ** 30)], "out of range"), ([(1.0, 2)], "two integers"), ([(1.5, 2)], "two integers"),
                       ([("1", 2)], "two integers"), ([(True, 2)], "two integers"), ([(1, 2, 3)], "two integers"),
                       ([(1,)], "two integers"), ([(1, None)], "two integers"), ([(1, math.nan)], "two integers"),
                       ([(1, 1), (1, 1)], "Duplicate seat positions")]
        for layout, text in bad_layouts:
            expect_error(lambda: history.set_seats("A", [(2, 2)] + layout), text)
            assert history.get_seats("A") == good, layout              #出错时原座位表不变
        #列表形式的坐标也可以（JSON读出来的就是列表）
        history.set_seats("A", [[1, 1], [1, 2]])
        assert history.get_seats("A") == [(1, 1), (1, 2)]

        #数量上限：座位500个，在班学生500人（这里把学生上限临时调小来测试）
        full = [(x, y) for x in range(1, 26) for y in range(20)]
        history.set_seats("A", full)
        assert len(history.get_seats("A")) == 500
        expect_error(lambda: history.set_seats("A", full + [(26, 0)]), "Too many seats")
        assert len(history.get_seats("A")) == 500
        saved = m.config.MAX_STUDENTS
        try:
            m.config.MAX_STUDENTS = 3
            history.add_student("A", "王五")
            expect_error(lambda: history.add_student("A", "赵六"), "Too many students")
            #上限只计在班的学生：有人离开后可以再加；满员时离开的人不能回来
            history.set_student_active("A", "王五", False)
            history.add_student("A", "赵六")
            expect_error(lambda: history.set_student_active("A", "王五", True), "Too many students")
            assert history.get_roster("A") == [("张 三", True), ("李 四", True), ("王五", False), ("赵六", True)]
        finally:
            m.config.MAX_STUDENTS = saved
    print("名称、坐标范围与数量上限测试通过")


def test_transactions(folder):
    #事务：多步操作合成一个整体；数据库被占用时明确报错
    path = folder / "transaction.db"
    with m.HistoryManager(path) as history:
        history.add_class("A")
        history.set_seats("A", grid_positions(2, 3))

        #事务中途出错：之前的各步全部撤销（包括嵌套在里面的方法各自的事务）
        try:
            with history.transaction():
                history.add_student("A", "甲")
                history.add_student("A", "乙")
                history.set_seats("A", [(1, 1)])
                history.add_student("A", "甲")          #重名，报错
            raise AssertionError("没有抛出预期的错误")
        except ValueError as error:
            assert "Student already exists" in str(error)
        assert history.get_students("A") == [] and len(history.get_seats("A")) == 6
        assert not history.conn.in_transaction

        #事务正常结束：全部生效
        with history.transaction():
            history.add_student("A", "甲")
            history.add_student("A", "乙")
        assert history.get_students("A") == ["甲", "乙"]

        #另一个连接正在写入时：等待超时后报sqlite3.OperationalError，数据不变
        #（正常的等待时间是5秒，这里调短以免测试太慢）
        history.conn.execute("PRAGMA busy_timeout = 200")
        other = sqlite3.connect(path, isolation_level=None)
        other.execute("BEGIN IMMEDIATE")
        try:
            history.add_student("A", "丙")
            raise AssertionError("没有抛出预期的错误")
        except sqlite3.OperationalError as error:
            assert "locked" in str(error)
        #读取不受影响
        assert history.get_students("A") == ["甲", "乙"] and not history.conn.in_transaction
        other.execute("ROLLBACK")
        other.close()
        #对方放开之后可以正常写入
        history.add_student("A", "丙")
        assert history.get_students("A") == ["甲", "乙", "丙"]
    print("事务测试通过")


def test_versions_and_backup(folder):
    #数据库版本、旧版本自动升级、自动备份
    #用一个单独的文件夹，避免与前面的测试留下的备份混在一起
    folder = folder / "versions"
    folder.mkdir()

    #新建的数据库带有当前版本号
    path = folder / "version.db"
    with m.HistoryManager(path) as history:
        assert history.conn.execute("PRAGMA user_version").fetchone()[0] == 2
        history.add_class("A")
    assert not (folder / "backups").exists()        #新建数据库不产生备份

    #造一个版本1的旧数据库：1个班、4个座位、3名学生（其中1名已离开）、2次方案
    old_path = folder / "old" / "seats.db"
    old_path.parent.mkdir()
    conn = sqlite3.connect(old_path)
    conn.executescript(V1_SCHEMA)
    conn.execute("INSERT INTO classes VALUES (1, '旧班')")
    conn.executemany("INSERT INTO seats VALUES (1, ?, ?)", [(1, 0), (1, 1), (2, 0), (2, 1)])
    conn.executemany("INSERT INTO students VALUES (?, 1, ?, ?)", [(1, "甲", 1), (2, "乙", 1), (3, "丙", 0)])
    conn.executemany("INSERT INTO assignments VALUES (?, 1, ?, ?)",
                     [(1, -0.5, "2026-01-01T08:00:00"), (2, -0.25, "2026-01-08T08:00:00")])
    conn.executemany("INSERT INTO assignment_entries VALUES (?, ?, ?, ?, ?)",
                     [(1, 1, 1, 0, 0.5), (1, 2, 1, 1, -0.5), (1, 3, 2, 0, 1.0), (2, 1, 2, 1, 0.25), (2, 2, 2, 0, 0.5)])
    conn.commit()
    conn.close()
    old_bytes = old_path.read_bytes()

    #打开时自动升级：数据原样保留，学生的历史负担仍是最近一条记录的值
    with m.HistoryManager(old_path) as history:
        assert history.conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert history.get_classes() == ["旧班"] and len(history.get_seats("旧班")) == 4
        assert history.get_roster("旧班") == [("甲", True), ("乙", True), ("丙", False)]
        assert history.load_state("旧班") == {
            "甲": {"seat_history": [(1, 0), (2, 1)], "accum_burden": 0.25, "current_seat": (2, 1)},
            "乙": {"seat_history": [(1, 1), (2, 0)], "accum_burden": 0.5, "current_seat": (2, 0)}}
        assert len(history.query_assignments()) == 2 and history.check_integrity() == []
        #升级后可以正常继续排座
        run_round(history, "旧班", m.load_class(history, "旧班")[2])
    #升级之前留下了一份备份，内容就是升级前的旧文件（仍是版本1）
    backups = list((old_path.parent / "backups").glob("seats_*.db"))
    assert len(backups) == 1
    conn = sqlite3.connect(backups[0])
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert [row[1] for row in conn.execute("PRAGMA table_info(students)")] == ["id", "class_id", "student_id", "active"]
    assert conn.execute("SELECT COUNT(*) FROM assignment_entries").fetchone()[0] == 5
    conn.close()
    assert len(old_bytes) > 0
    #再次打开已经是当前版本，不再升级、不再备份
    with m.HistoryManager(old_path) as history:
        assert len(history.query_assignments()) == 3
    assert len(list((old_path.parent / "backups").glob("seats_*.db"))) == 1

    #更新版本的程序建立的数据库：拒绝打开，文件不变
    newer = folder / "newer.db"
    with m.HistoryManager(newer):
        pass
    conn = sqlite3.connect(newer)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    expect_error(lambda: m.HistoryManager(newer), "newer version")
    #版本号对但表结构不对：不是本程序的数据库
    conn = sqlite3.connect(newer)
    conn.execute("PRAGMA user_version = 2")
    conn.execute("ALTER TABLE classes ADD COLUMN extra TEXT")
    conn.close()
    expect_error(lambda: m.HistoryManager(newer), "Not a seat history database")

    #手动备份：返回备份文件；只保留最近10份
    with m.HistoryManager(path) as history:
        made = [history.backup() for _ in range(12)]
        kept = sorted((folder / "backups").glob("version_*.db"))
        assert kept == made[-10:] and all(p.exists() for p in kept)

        #删除班级之前自动备份：备份里还有这个班级
        history.set_seats("A", grid_positions(2, 2))
        history.add_student("A", "甲")
        run_round(history, "A", m.load_class(history, "A")[2])
        history.delete_class("A")
        assert history.get_classes() == []
        newest = sorted((folder / "backups").glob("version_*.db"))[-1]
        assert newest not in made and len(list((folder / "backups").glob("version_*.db"))) == 10
        #删除不存在的班级：报错，且不产生备份
        expect_error(lambda: history.delete_class("A"), "Unknown class")
        assert sorted((folder / "backups").glob("version_*.db"))[-1] == newest
    with m.HistoryManager(newest) as backup:
        assert backup.get_classes() == ["A"] and backup.get_students("A") == ["甲"]
        assert len(backup.query_assignments(class_name="A")) == 1
    print("版本升级与备份测试通过")


def test_corrupted_data(folder):
    #数据库被别的程序改坏：完整性检查能发现，读取时明确报错而不是把坏数据带进计算
    path = folder / "corrupt.db"

    def fresh():
        #重新建立一个正常的数据库：1个班、6个座位、4名学生、2次方案
        if path.exists():
            path.unlink()
        with m.HistoryManager(path) as history:
            classroom, students, alloc = build_class(history, "A", grid_positions(2, 3), 4)
            run_round(history, "A", alloc)
            run_round(history, "A", alloc)
            assert history.check_integrity() == []

    def damage(sql):
        #绕过HistoryManager直接改数据库（这个连接没有开启外键检查）
        conn = sqlite3.connect(path)
        conn.execute(sql)
        conn.commit()
        conn.close()

    #每一项：(改坏数据的语句, 完整性检查应报告的问题, 读取班级状态时应报的错误；None表示读取不受影响)
    cases = [
        ("UPDATE assignment_entries SET burden = 'abc' WHERE assignment_id = 2 AND student = 1",
         "Burden is not a finite number", "Corrupted record: class A, student A-0, assignment 2"),
        ("UPDATE assignment_entries SET burden = 9e999 WHERE assignment_id = 1 AND student = 2",
         "Burden is not a finite number", "Corrupted record: class A, student A-1, assignment 1"),
        ("UPDATE assignment_entries SET x = 'left' WHERE assignment_id = 2 AND student = 3",
         "Seat position in assignment is not an integer", "Corrupted record: class A, student A-2, assignment 2"),
        ("UPDATE students SET base_burden = 'x' WHERE id = 4",
         "Burden base is not valid", "Corrupted record: class A, student A-3, burden base"),
        ("UPDATE seats SET y = 'a' WHERE x = 1 AND y = 0",
         "Seat position is not an integer", "Corrupted seat position in class A"),
        ("UPDATE assignments SET score = 'high' WHERE id = 1", "Score is not a finite number", None),
        ("UPDATE assignments SET created_at = '昨天' WHERE id = 2", "Time is not valid: assignment 2", None),
        ("UPDATE students SET active = 5 WHERE id = 1", "Active flag is not 0 or 1", None),
        ("DELETE FROM assignment_entries WHERE assignment_id = 1", "Assignment has no content: assignment 1", None),
    ]
    for sql, problem, load_error in cases:
        fresh()
        damage(sql)
        with m.HistoryManager(path) as history:
            problems = history.check_integrity()
            assert len(problems) == 1 and problem in problems[0], (sql, problems)
            if load_error is None:
                m.load_class(history, "A")
            else:
                expect_error(lambda: m.load_class(history, "A"), load_error)

    #保存方案时也不接受异常的数值
    fresh()
    with m.HistoryManager(path) as history:
        classroom, students, alloc = m.load_class(history, "A")
        best, score = alloc.select_best_assignment()
        for bad_score in (math.nan, math.inf, "1"):
            record = m.SeatAssignment(None)
            record.set_seat_assignment(best, 0.0)
            record.score = bad_score
            expect_error(lambda: history.record_assignment("A", record), "Record score must be a finite number")
        record = m.SeatAssignment(None)
        record.set_seat_assignment(best, score)
        record.assignments["A-0"]["burden"] = math.nan
        expect_error(lambda: history.record_assignment("A", record), "Burden must be a finite number")
        assert len(history.query_assignments()) == 2
    print("数据损坏的检查与报错测试通过")


if __name__ == "__main__":
    #ignore_cleanup_errors不设置：临时文件清除失败（例如数据库没有关闭）应当报错而不是被忽略
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        test_files(folder)
        test_restore(folder)
        test_queries(folder)
        test_roster_and_layout(folder)
        test_errors_and_integrity(folder)
        test_management(folder)
        test_names_and_limits(folder)
        test_transactions(folder)
        test_versions_and_backup(folder)
        test_corrupted_data(folder)
    print("全部通过")
