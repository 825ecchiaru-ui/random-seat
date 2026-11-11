#网页服务各接口的正确性测试（不需要启动服务，用Django自带的测试客户端直接调用）
#用法：python tests/test_web.py
#测试用的数据库建在系统临时目录中，不会动项目data文件夹里的数据库
import json
import os
import random
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote

from layout_tools import m, grid_positions

os.environ["DJANGO_SETTINGS_MODULE"] = "web.settings"
import django
django.setup()
from django.conf import settings
from django.test import Client
from web import views

#测试中有大量故意发出的错误请求，Django会为每一个打印一行"Bad Request"提示。
#这些提示不是错误（请求是否被正确拒绝由下面的断言检查），这里只保留真正的服务器错误信息
import logging
logging.getLogger("django.request").setLevel(logging.ERROR)
logging.getLogger("django.security.csrf").setLevel(logging.ERROR)     #同理：故意不带令牌的那一次请求

#测试客户端默认的主机名不在允许访问的范围内，这里指定为本机
client = Client(SERVER_NAME="127.0.0.1")


def call(method, path, body=None, expect=200):
    #发一个请求，确认状态码符合预期，返回解析后的JSON
    #没有内容的请求发送空内容（与浏览器的行为一致；测试客户端默认会发一份表单格式的内容）
    kwargs = {"data": "" if body is None else json.dumps(body), "content_type": "application/json"}
    if method == "get":
        kwargs = {}
    response = getattr(client, method)("/api/" + quote(path), **kwargs)
    assert response.status_code == expect, (method, path, response.status_code, response.content[:300])
    return json.loads(response.content)


def expect_error(method, path, body, text):
    #确认请求被拒绝（状态码400），且错误信息包含text
    result = call(method, path, body, expect=400)
    assert text in result["error"], result


def plan_of(result):
    #从预览或打分的结果中取出方案{student_id: [x, y]}，即确认时要发回的形式
    return {sid: info["current_seat"] for sid, info in result["assignments"].items()}


def test_page_and_token():
    #网页能打开，并且给浏览器发了防伪令牌
    response = client.get("/")
    assert response.status_code == 200 and "csrftoken" in response.cookies
    assert "排座" in response.content.decode("utf-8")

    #开启令牌检查的客户端（浏览器的真实情况）：不带令牌的修改请求被拒绝，带上令牌则通过
    strict = Client(SERVER_NAME="127.0.0.1", enforce_csrf_checks=True)
    token = strict.get("/").cookies["csrftoken"].value
    body = {"data": json.dumps({"name": "令牌测试"}), "content_type": "application/json"}
    assert strict.post("/api/classes", **body).status_code == 403
    assert call("get", "classes")["classes"] == []
    assert strict.post("/api/classes", **body, HTTP_X_CSRFTOKEN=token).status_code == 200
    assert call("get", "classes")["classes"] == ["令牌测试"]
    call("delete", "classes/令牌测试")
    print("网页与防伪令牌测试通过")


def test_setup():
    #建立班级、座位表和名单
    created = call("post", "classes", {"name": "  高一3班 "})
    assert created == {"classes": ["高一3班"], "name": "高一3班"}            #首尾空格被去掉，返回整理后的名称
    expect_error("post", "classes", {"name": "高一3班"}, "Class already exists")
    expect_error("post", "classes", {"name": "   "}, "Name must not be empty")
    expect_error("post", "classes", {"name": "a/b"}, "must not contain")
    expect_error("post", "classes", {}, "Name must not be empty")
    expect_error("post", "classes", {"name": 123}, "Name must not be empty")
    expect_error("post", "classes", {"name": ".."}, "must not contain")
    expect_error("post", "classes", {"name": "高一\n3班"}, "invisible or control")
    expect_error("post", "classes", {"name": "高一\u200b3班"}, "invisible or control")
    expect_error("post", "classes", {"name": "x" * 51}, "Name is too long")
    assert call("get", "classes")["classes"] == ["高一3班"]
    expect_error("get", "classes/没有这个班", None, "Unknown class")

    #刚建立的空班级
    info = call("get", "classes/高一3班")["class"]
    assert info == {"name": "高一3班", "blackboard": None, "seats": [], "students": [], "latest": None}
    expect_error("post", "classes/高一3班/preview", None, "Class has no students")

    #座位表
    positions = [list(p) for p in grid_positions(3, 4)]
    info = call("put", "classes/高一3班/seats", {"positions": positions})["class"]
    assert [[s["x"], s["y"]] for s in info["seats"]] == positions
    assert info["blackboard"] == [0, 1.5]
    assert all(-1 <= s["burden"] <= 1 and s["student"] is None for s in info["seats"])
    for bad in ({"positions": [[1, 1], [1, 1]]}, {"positions": [[1, "a"]]}, {"positions": [[1, 2, 3]]},
                {"positions": [[1.5, 2]]}, {"positions": [[True, 2]]}, {"positions": "x"}, {},
                {"positions": [1, 2]}, {"positions": [[0, 1]]}, {"positions": [[1, -1]]},
                {"positions": [[1, 101]]}, {"positions": [[1, 10 ** 9]]}, {"positions": [[1, 10 ** 30]]},
                {"positions": [[x, y] for x in range(1, 27) for y in range(20)]}):       #520个，超过上限
        call("put", "classes/高一3班/seats", bad, expect=400)
    assert len(call("get", "classes/高一3班")["class"]["seats"]) == 12      #出错的请求没有改动座位表

    #名单：一次添加多个；其中有一个不合格时一个都不添加
    names = [f"学生{i}" for i in range(1, 10)]
    info = call("post", "classes/高一3班/students", {"student_ids": names})["class"]
    assert [s["student_id"] for s in info["students"]] == names
    assert all(s == {"student_id": s["student_id"], "active": True, "accum_burden": 0.0,
                     "rounds": 0, "current_seat": None} for s in info["students"])
    expect_error("post", "classes/高一3班/students", {"student_ids": ["新来的", "学生3"]}, "Student already exists")
    expect_error("post", "classes/高一3班/students", {"student_ids": ["甲", "甲"]}, "Student already exists")
    expect_error("post", "classes/高一3班/students", {"student_ids": ["乙", ""]}, "Name must not be empty")
    expect_error("post", "classes/高一3班/students", {"student_ids": []}, "non-empty list")
    expect_error("post", "classes/高一3班/students", {"student_ids": ["丙", 123]}, "Name must not be empty")
    expect_error("post", "classes/高一3班/students", {"student_ids": ["丁", "学生 3", "学生\u30003"]},
                 "Student already exists")       #整理空格后重名
    assert len(call("get", "classes/高一3班")["class"]["students"]) == 9
    print("班级、座位表、名单测试通过")


def test_arrange():
    #预览 -> 手动对调 -> 确认 -> 历史 -> 撤销
    before = call("get", "classes/高一3班")["class"]

    #预览：可以反复生成，不改变任何数据
    random.seed(21)
    first = call("post", "classes/高一3班/preview")
    second = call("post", "classes/高一3班/preview")
    assert call("get", "classes/高一3班")["class"] == before
    assert call("get", "classes/高一3班/assignments") == {"assignments": [], "has_more": False}
    assert set(first["assignments"]) == {s["student_id"] for s in before["students"]}
    assert len({tuple(p) for p in plan_of(first).values()}) == 9 and first["score"] <= 0

    #预览的结果与直接调用后端得到的一致：把预览的方案交给后端打分，分数和负担都相同
    with m.HistoryManager(settings.SEAT_DB_PATH) as history:
        classroom, students, alloc = m.load_class(history, "高一3班")
        seat_at = {(s.x, s.y): s for s in classroom.seats}
        student_of = {s.student_id: s for s in students}
        assignment = {seat_at[tuple(p)]: student_of[sid] for sid, p in plan_of(second).items()}
        assert abs(alloc.score_assignment(assignment) - second["score"]) < 1e-12
        for seat, stu in assignment.items():
            assert second["assignments"][stu.student_id]["burden"] == stu.accum_burden + seat.burden

    #原样打分：与预览相同
    plan = plan_of(second)
    assert first["base"] == 0 and second["base"] == 0        #还没有任何方案
    assert call("post", "classes/高一3班/score", {"plan": plan, "base": 0}) == second

    #手动对调两个学生，再把一个学生移到空座位上：分数按新方案重算
    plan["学生1"], plan["学生2"] = plan["学生2"], plan["学生1"]
    empty = next(p for p in ([s["x"], s["y"]] for s in before["seats"]) if p not in plan.values())
    plan["学生3"] = empty
    adjusted = call("post", "classes/高一3班/score", {"plan": plan, "base": 0})
    assert plan_of(adjusted) == plan
    burden_at = {(s["x"], s["y"]): s["burden"] for s in before["seats"]}
    for sid, info in adjusted["assignments"].items():
        assert info["burden"] == burden_at[tuple(info["current_seat"])]      #第一轮：历史负担就是座位负担
    values = [info["burden"] for info in adjusted["assignments"].values()]
    mean = sum(values) / len(values)
    expected = -(sum((v - mean) ** 2 for v in values) / len(values) + max(values) - min(values))
    assert abs(adjusted["score"] - expected) < 1e-9

    #不合格的方案：打分和确认都拒绝，且数据不变
    taken = plan["学生2"]
    bad_plans = [
        ({k: v for k, v in plan.items() if k != "学生9"}, "every active student"),     #少了人
        (dict(plan, 外人=[1, 0]), "every active student"),                              #多了人
        (dict(plan, 学生1=[9, 9]), "Seat not in class layout"),                         #座位不存在
        (dict(plan, 学生1=taken), "A seat cannot hold multiple students"),              #两人同座
        (dict(plan, 学生1="1,0"), "two integers"),                                      #坐标格式不对
        (None, "plan must be an object"),
    ]
    for bad, text in bad_plans:
        expect_error("post", "classes/高一3班/score", {"plan": bad, "base": 0}, text)
        expect_error("post", "classes/高一3班/assignments", {"plan": bad, "base": 0}, text)
    #没有带上base，或base不是整数
    for body in ({"plan": plan}, {"plan": plan, "base": "0"}, {"plan": plan, "base": None}):
        expect_error("post", "classes/高一3班/score", body, "base must be an integer")
        expect_error("post", "classes/高一3班/assignments", body, "base must be an integer")
    assert call("get", "classes/高一3班")["class"] == before

    #确认调整后的方案：保存成功，班级状态变成这个方案
    result = call("post", "classes/高一3班/assignments", {"plan": plan, "base": 0})
    info = result["class"]
    assert info["latest"]["assignment_id"] == result["assignment_id"]
    assert abs(info["latest"]["score"] - adjusted["score"]) < 1e-12
    for s in info["students"]:
        assert s["current_seat"] == plan[s["student_id"]] and s["rounds"] == 1
        assert s["accum_burden"] == adjusted["assignments"][s["student_id"]]["burden"]
    seated = {(s["x"], s["y"]): s["student"] for s in info["seats"]}
    assert {sid: list(pos) for pos, sid in seated.items() if sid} == plan
    assert sum(sid is None for sid in seated.values()) == 3
    assert call("get", "classes/高一3班")["class"] == info

    #再排4轮（每轮都是预览后直接确认），历史负担逐轮累加
    snapshots = [info]
    for _ in range(4):
        previewed = call("post", "classes/高一3班/preview")
        assert previewed["base"] == info["latest"]["assignment_id"]
        info = call("post", "classes/高一3班/assignments",
                    {"plan": plan_of(previewed), "base": previewed["base"]})["class"]
        for s in info["students"]:
            assert s["accum_burden"] == previewed["assignments"][s["student_id"]]["burden"]
        snapshots.append(info)
    assert all(s["rounds"] == 5 for s in info["students"])

    #历史列表：新的在前；limit控制数量，has_more表示还有更早的
    listed = call("get", "classes/高一3班/assignments")
    assert len(listed["assignments"]) == 5 and not listed["has_more"]
    ids = [a["assignment_id"] for a in listed["assignments"]]
    assert ids == sorted(ids, reverse=True) and ids[0] == info["latest"]["assignment_id"]
    assert plan_of(listed["assignments"][-1]) == plan
    response = client.get("/api/" + quote("classes/高一3班/assignments") + "?limit=2")
    limited = json.loads(response.content)
    assert [a["assignment_id"] for a in limited["assignments"]] == ids[:2] and limited["has_more"]
    for bad in ("0", "abc", "-3", "201", "9" * 30):
        assert client.get("/api/" + quote("classes/高一3班/assignments") + "?limit=" + bad).status_code == 400

    #单个学生的历史
    track = call("get", "classes/高一3班/students/学生1/history")["history"]
    assert len(track) == 5 and track[0]["seat"] == plan["学生1"]
    assert track[-1]["burden"] == next(s for s in info["students"] if s["student_id"] == "学生1")["accum_burden"]
    assert [t["assignment_id"] for t in track] == sorted(ids)

    #撤销：逐次回到之前每一轮的状态
    for rnd in range(4, 0, -1):
        target = snapshots[rnd]["latest"]["assignment_id"]
        result = call("delete", "classes/高一3班/assignments/latest", {"assignment_id": target})
        assert result["assignment_id"] == target
        assert result["class"] == snapshots[rnd - 1]
    call("delete", "classes/高一3班/assignments/latest", {"assignment_id": snapshots[0]["latest"]["assignment_id"]})
    assert call("get", "classes/高一3班")["class"] == before
    expect_error("delete", "classes/高一3班/assignments/latest", {"assignment_id": 0}, "No assignment to roll back")
    expect_error("delete", "classes/高一3班/assignments/latest", None, "base must be an integer")
    print("预览、对调、确认、历史、撤销测试通过")


def test_roster_and_class():
    #先排两轮，留下历史
    for _ in range(2):
        previewed = call("post", "classes/高一3班/preview")
        call("post", "classes/高一3班/assignments", {"plan": plan_of(previewed), "base": previewed["base"]})

    #学生离开：仍在名单中但标记为不在班，保留负担和次数，不再参与排座
    info = call("put", "classes/高一3班/students/学生9", {"active": False})["class"]
    gone = info["students"][-1]
    assert gone["student_id"] == "学生9" and not gone["active"] and gone["rounds"] == 2
    assert gone["current_seat"] is None and "学生9" not in [s["student"] for s in info["seats"]]
    assert "学生9" not in call("post", "classes/高一3班/preview")["assignments"]
    expect_error("put", "classes/高一3班/students/学生9", {"active": "no"}, "active must be true or false")
    #回来：历史负担从其余在班学生当前的平均值开始，排座次数（座位历史）保留
    others = [s["accum_burden"] for s in info["students"] if s["active"]]
    info = call("put", "classes/高一3班/students/学生9", {"active": True})["class"]
    back = info["students"][-1]
    #离开期间没有排过座，最新方案里还有他，所以回来后仍在原来的座位上
    assert back["active"] and back["rounds"] == 2 and back["current_seat"] is not None
    assert abs(back["accum_burden"] - sum(others) / len(others)) < 1e-12
    assert abs(back["accum_burden"] - gone["accum_burden"]) > 1e-9

    #改学生编号：历史保留
    info = call("put", "classes/高一3班/students/学生1", {"student_id": " 张三 "})["class"]
    assert info["students"][0]["student_id"] == "张三" and info["students"][0]["rounds"] == 2
    assert len(call("get", "classes/高一3班/students/张三/history")["history"]) == 2
    expect_error("put", "classes/高一3班/students/张三", {"student_id": "学生2"}, "Student already exists")
    expect_error("put", "classes/高一3班/students/学生1", {"student_id": "x"}, "Unknown student")

    #删除学生：有历史的不能删，从未入座的可以删
    expect_error("delete", "classes/高一3班/students/张三", None, "Student has history")
    #中途加入的学生：历史负担同样从全班在班学生当前的平均值开始
    everyone = [s["accum_burden"] for s in info["students"]]
    added = call("post", "classes/高一3班/students", {"student_ids": ["录错的"]})["class"]["students"][-1]
    assert added["rounds"] == 0 and abs(added["accum_burden"] - sum(everyone) / len(everyone)) < 1e-12
    info = call("delete", "classes/高一3班/students/录错的")["class"]
    assert len(info["students"]) == 9

    #学生多于座位时不能生成方案
    call("put", "classes/高一3班/seats", {"positions": [[1, 0], [1, 1]]})
    expect_error("post", "classes/高一3班/preview", None, "Not enough seats")
    call("put", "classes/高一3班/seats", {"positions": [list(p) for p in grid_positions(3, 4)]})

    #班级改名：内容保留
    call("post", "classes", {"name": "另一个班"})
    result = call("put", "classes/高一3班", {"name": "高二3班"})
    assert result["classes"] == ["高二3班", "另一个班"]
    assert result["class"] == dict(info, name="高二3班", seats=result["class"]["seats"])
    assert len(call("get", "classes/高二3班/assignments")["assignments"]) == 2
    expect_error("put", "classes/高二3班", {"name": "另一个班"}, "Class already exists")
    expect_error("get", "classes/高一3班", None, "Unknown class")

    #数据完整
    assert call("get", "integrity") == {"problems": []}

    #删除班级
    assert call("delete", "classes/高二3班")["classes"] == ["另一个班"]
    expect_error("get", "classes/高二3班", None, "Unknown class")
    assert call("get", "integrity") == {"problems": []}
    print("名单管理与班级改名、删除测试通过")


def test_stale_operations():
    #过期的操作：页面上的数据已经不是最新的（连点两次、在另一个网页里操作过），服务器应当拒绝
    name = "并发班"
    call("post", "classes", {"name": name})
    call("put", f"classes/{name}/seats", {"positions": [list(p) for p in grid_positions(2, 3)]})
    call("post", f"classes/{name}/students", {"student_ids": ["a", "b", "c", "d"]})
    count = lambda: len(call("get", f"classes/{name}/assignments")["assignments"])

    #同一个预览连续确认两次：第二次被拒绝，只保存了一条记录
    p1 = call("post", f"classes/{name}/preview")
    body = {"plan": plan_of(p1), "base": p1["base"]}
    id1 = call("post", f"classes/{name}/assignments", body)["assignment_id"]
    expect_error("post", f"classes/{name}/assignments", body, "Class data has changed")
    expect_error("post", f"classes/{name}/score", body, "Class data has changed")
    assert count() == 1

    #两个网页各自预览，一个先确认了，另一个再确认时被拒绝
    p2 = call("post", f"classes/{name}/preview")
    p3 = call("post", f"classes/{name}/preview")
    assert p2["base"] == p3["base"] == id1
    id2 = call("post", f"classes/{name}/assignments", {"plan": plan_of(p3), "base": p3["base"]})["assignment_id"]
    expect_error("post", f"classes/{name}/assignments", {"plan": plan_of(p2), "base": p2["base"]}, "Class data has changed")
    assert count() == 2

    #撤销：页面以为最新的是id1（其实已经是id2）时被拒绝；撤销id2之后，再撤销id2也被拒绝
    expect_error("delete", f"classes/{name}/assignments/latest", {"assignment_id": id1}, "Class data has changed")
    assert count() == 2
    assert call("delete", f"classes/{name}/assignments/latest", {"assignment_id": id2})["assignment_id"] == id2
    expect_error("delete", f"classes/{name}/assignments/latest", {"assignment_id": id2}, "Class data has changed")
    assert count() == 1

    #预览之后名单变了（别处加了人）：方案里没有新学生，确认被拒绝
    p4 = call("post", f"classes/{name}/preview")
    call("post", f"classes/{name}/students", {"student_ids": ["新人"]})
    expect_error("post", f"classes/{name}/assignments", {"plan": plan_of(p4), "base": p4["base"]}, "every active student")

    #预览之后座位表变了（方案用到的座位被去掉）：确认被拒绝
    p5 = call("post", f"classes/{name}/preview")
    used = plan_of(p5)["a"]
    remaining = [list(p) for p in grid_positions(2, 3) if list(p) != used]
    call("put", f"classes/{name}/seats", {"positions": remaining})
    expect_error("post", f"classes/{name}/assignments", {"plan": plan_of(p5), "base": p5["base"]}, "Seat not in class layout")
    assert count() == 1 and call("get", "integrity") == {"problems": []}

    #人数上限：一次添加的学生里只要超出上限，整批都不添加
    saved = m.config.MAX_STUDENTS
    try:
        m.config.MAX_STUDENTS = 6
        expect_error("post", f"classes/{name}/students", {"student_ids": ["x1", "x2"]}, "Too many students")
        assert len(call("get", f"classes/{name}")["class"]["students"]) == 5
    finally:
        m.config.MAX_STUDENTS = saved
    call("delete", f"classes/{name}")
    print("过期操作与重复提交测试通过")


def test_server_failures():
    #服务器端出问题时，返回的仍是JSON格式的错误说明（不是一整页错误网页），并且记入日志
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    web_logger = logging.getLogger("web")
    web_logger.addHandler(handler)
    web_logger.propagate = False        #测试中不把这些预期内的错误再打印到屏幕上
    #Django自己也会为每个5xx响应打印一行提示，这两次是故意制造的，同样不打印
    logging.getLogger("django.request").setLevel(logging.CRITICAL)
    try:
        #数据库被别的程序占用：等待约5秒后返回503
        locker = sqlite3.connect(settings.SEAT_DB_PATH, isolation_level=None)
        locker.execute("BEGIN IMMEDIATE")
        result = call("post", "classes", {"name": "占用时新建"}, expect=503)
        locker.execute("ROLLBACK")
        locker.close()
        assert "Database is busy or unavailable" in result["error"]
        assert len(records) == 1 and "数据库无法读写" in records[0].getMessage()
        assert "占用时新建" not in call("get", "classes")["classes"]
        #占用解除后恢复正常
        call("post", "classes", {"name": "占用时新建"})
        call("delete", "classes/占用时新建")

        #程序里没有预料到的错误：返回500，日志里有完整的出错位置
        original = views.describe_class
        views.describe_class = lambda history, class_name: 1 / 0
        try:
            result = call("get", "classes/另一个班", expect=500)
        finally:
            views.describe_class = original
        assert result == {"error": "Internal server error"}
        assert len(records) == 2 and records[1].exc_info[0] is ZeroDivisionError
    finally:
        web_logger.removeHandler(handler)
        web_logger.propagate = True
        logging.getLogger("django.request").setLevel(logging.ERROR)

    #程序信息：网页版不是桌面版，不透露数据文件夹的位置；"打开文件夹"这类桌面版功能不可用
    info = call("get", "app")
    assert info == {"version": m.__version__, "desktop": False, "data_dir": ""}
    for what in ("data", "help", "C:\Windows", None):
        expect_error("post", "app/open", {"what": what}, "Only available in the desktop app")
    #模拟桌面版：只能打开固定的两个位置，不接受别的；使用说明文件不存在时报错而不是出别的问题
    settings.DESKTOP_MODE = True
    try:
        assert call("get", "app")["desktop"] is True and call("get", "app")["data_dir"] == str(settings.DATA_DIR)
        for what in ("C:\Windows", "../", "", None, 5):
            expect_error("post", "app/open", {"what": what}, "Cannot open")
        expect_error("post", "app/open", {"what": "help"}, "Cannot open")      #测试环境里没有使用说明文件
    finally:
        settings.DESKTOP_MODE = False

    #JSON嵌套层数极多、请求内容过大：各自被拒绝
    response = client.post("/api/classes", data="[" * 100000 + "]" * 100000, content_type="application/json")
    assert response.status_code == 400 and "nested too deeply" in json.loads(response.content)["error"]
    response = client.post("/api/classes", data=json.dumps({"name": "x" * 3_000_000}), content_type="application/json")
    assert response.status_code == 413 and "too large" in json.loads(response.content)["error"]
    assert call("get", "classes")["classes"] == ["另一个班"]
    print("服务器故障处理测试通过")


def test_bad_requests():
    #请求方式不对：405
    assert client.delete("/api/classes").status_code == 405
    assert client.get("/api/" + quote("classes/另一个班/preview")).status_code == 405
    #内容不是JSON，或不是对象
    response = client.post("/api/classes", data="{不是JSON", content_type="application/json")
    assert response.status_code == 400 and "error" in json.loads(response.content)
    response = client.post("/api/classes", data="[1, 2]", content_type="application/json")
    assert response.status_code == 400 and "JSON object" in json.loads(response.content)["error"]
    print("错误请求测试通过")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temp:
        #把服务使用的数据库换成临时目录中的文件
        settings.SEAT_DB_PATH = Path(temp) / "web_test.db"
        test_page_and_token()
        test_setup()
        test_arrange()
        test_roster_and_class()
        test_stale_operations()
        test_server_failures()
        test_bad_requests()
    print("全部通过")
