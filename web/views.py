#各个接口的处理函数。这里只做三件事：检查浏览器发来的内容、调用seat_random包、把结果转成JSON
#排座的逻辑和数据的检查（名称、坐标范围、数量上限等）全部在seat_random包中，这里不重复实现
#
#服务不保留状态：每个请求都打开数据库、按其中的内容重建班级，处理完就关闭。
#这样服务重启、同时打开多个页面都不会出现内存与数据库不一致
import json
import logging
import os
import sqlite3
from functools import wraps

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

import seat_random
from seat_random import HistoryManager, SeatAssignment, load_class

logger = logging.getLogger("web")

#历史页一次最多读取的方案数量
MAX_QUERY_LIMIT = 200


def api(view):
    #接口处理函数共用的外壳：
    #  1. 把请求内容解析成字典data
    #  2. 打开数据库，把history和data交给处理函数，处理完自动关闭数据库
    #  3. 处理函数返回的字典转成JSON
    #出错时一律返回JSON格式的{"error": 说明}，界面据此显示提示，不会收到一整页的错误网页：
    #  输入有误或数据已变化（ValueError）   状态码400
    #  请求内容过大                       状态码413
    #  数据库被占用或无法读写              状态码503，并记入日志
    #  其他没有预料到的错误                状态码500，并把完整的出错位置记入日志
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            data = json.loads(request.body) if request.body else {}
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")

            settings.SEAT_DB_PATH.parent.mkdir(exist_ok=True)
            with HistoryManager(settings.SEAT_DB_PATH) as history:
                result = view(request, history, data, *args, **kwargs)
        except ValueError as error:
            return JsonResponse({"error": str(error)}, status=400)
        except RecursionError:
            #JSON嵌套层数过多
            return JsonResponse({"error": "Request body is nested too deeply"}, status=400)
        except RequestDataTooBig:
            return JsonResponse({"error": "Request is too large"}, status=413)
        except sqlite3.OperationalError as error:
            logger.error("数据库无法读写（%s %s）：%s", request.method, request.path, error)
            return JsonResponse({"error": f"Database is busy or unavailable: {error}"}, status=503)
        except Exception:
            logger.exception("处理请求时出现未预料的错误（%s %s）", request.method, request.path)
            return JsonResponse({"error": "Internal server error"}, status=500)
        return JsonResponse(result)
    return wrapper


def is_integer(value):
    #是否为整数（True和False在Python里也算整数，这里要排除）
    return isinstance(value, int) and not isinstance(value, bool)


def text_of(value):
    #浏览器发来的名称必须是文字。名称本身是否合格（空、过长、特殊字符等）由后端的clean_name检查
    if not isinstance(value, str):
        raise ValueError("Name must not be empty")
    return value


def check_unchanged(history, class_name, base):
    #确认班级的排座数据自浏览器上次读取以来没有被别的操作改动过
    #base是浏览器读取数据时班级最新方案的编号（没有方案时为0）；每保存或撤销一次方案，这个编号都会变
    #用于拦住过期的操作：连点两次确认、在另一个网页里已经排过座或撤销过等
    if not is_integer(base):
        raise ValueError("base must be an integer")
    if history.latest_assignment_id(class_name) != base:
        raise ValueError("Class data has changed")


def describe_class(history, class_name):
    #读出一个班级的全部当前信息，供界面显示。多个接口在修改数据后都返回它，界面据此刷新
    classroom, students, allocator = load_class(history, class_name)
    student_of = {student.student_id: student for student in students}

    student_rows = []
    for student_id, active in history.get_roster(class_name):
        if active:
            student = student_of[student_id]
            seat = student.current_seat
            student_rows.append({
                "student_id": student_id,
                "active": True,
                "accum_burden": student.accum_burden,
                "rounds": len(student.seat_history),
                "current_seat": None if seat is None else [seat.x, seat.y],
            })
        else:
            #已离开的学生不在load_class的结果中，从历史记录里取他离开前的负担和排座次数
            track = history.get_student_history(class_name, student_id)
            student_rows.append({
                "student_id": student_id,
                "active": False,
                "accum_burden": track[-1]["burden"] if track else 0.0,
                "rounds": len(track),
                "current_seat": None,
            })

    latest = history.query_assignments(class_name=class_name, limit=1)
    return {
        "name": class_name,
        "blackboard": None if classroom.blackboard_pos is None else list(classroom.blackboard_pos),
        "seats": [{"x": seat.x, "y": seat.y, "burden": seat.burden,
                   "student": None if seat.student is None else seat.student.student_id}
                  for seat in classroom.seats],
        "students": student_rows,
        #最近一次方案的编号、时间和分数；还没有方案时为None
        "latest": ({key: latest[0][key] for key in ("assignment_id", "created_at", "score")}
                   if latest else None),
    }


def plan_to_assignment(classroom, students, plan):
    #把浏览器发来的方案{student_id: [x, y]}转成后端使用的方案字典{Seat: Student}
    #要求方案恰好包含全部在班学生，座位都属于本班座位表，且没有两个学生在同一个座位
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")

    seat_at = {(seat.x, seat.y): seat for seat in classroom.seats}
    student_of = {student.student_id: student for student in students}
    if set(plan) != set(student_of):
        raise ValueError("Plan must contain every active student exactly once")

    assignment = {}
    for student_id, value in plan.items():
        if not isinstance(value, list) or len(value) != 2 or not all(is_integer(v) for v in value):
            raise ValueError("A seat position must be two integers")
        position = tuple(value)
        if position not in seat_at:
            raise ValueError(f"Seat not in class layout: {position}")
        seat = seat_at[position]
        if seat in assignment:
            raise ValueError("A seat cannot hold multiple students")
        assignment[seat] = student_of[student_id]
    return assignment


def describe_plan(assignment, plan_score, base):
    #把一个还没有应用的方案转成界面使用的形式：
    #  {"score": 分数, "base": 生成时班级最新方案的编号,
    #   "assignments": {student_id: {"burden": 应用后的历史负担, "current_seat": [x, y]}}}
    #浏览器之后给这个方案打分或确认时要把base原样发回，服务器用它判断数据是否已经变化
    record = SeatAssignment(None)
    record.set_seat_assignment(assignment, plan_score)
    return {"score": record.score, "base": base, "assignments": record.assignments}


@ensure_csrf_cookie
def index(request):#网页本身。同时把防伪令牌放进浏览器，之后修改数据的请求要带上它
    #脚本和样式的网址后面带上版本号：程序升级后浏览器会重新读取它们，而不是继续用以前存下的旧文件
    return render(request, "index.html", {"version": seat_random.__version__})


@require_http_methods(["GET", "POST"])
@api
def classes(request, history, data):
    #GET：列出全部班级。POST：新建班级，内容为{"name": 班级名称}，返回中的"name"是整理后的名称
    if request.method == "POST":
        name = history.add_class(text_of(data.get("name")))
        return {"classes": history.get_classes(), "name": name}
    return {"classes": history.get_classes()}


@require_http_methods(["GET", "PUT", "DELETE"])
@api
def class_detail(request, history, data, class_name):
    #GET：班级的全部当前信息。PUT：改名，内容为{"name": 新名称}。DELETE：删除班级及其全部历史
    if request.method == "PUT":
        new_name = history.rename_class(class_name, text_of(data.get("name")))
        return {"classes": history.get_classes(), "class": describe_class(history, new_name)}
    if request.method == "DELETE":
        history.delete_class(class_name)        #删除前后端会自动备份数据库
        return {"classes": history.get_classes()}
    return {"class": describe_class(history, class_name)}


@require_http_methods(["PUT"])
@api
def seats(request, history, data, class_name):
    #设置座位表（整体替换），内容为{"positions": [[x, y], ...]}。坐标的范围和数量由后端检查
    positions = data.get("positions")
    if not isinstance(positions, list) or not all(isinstance(position, list) for position in positions):
        raise ValueError("positions must be a list of [x, y]")
    history.set_seats(class_name, positions)
    return {"class": describe_class(history, class_name)}


@require_http_methods(["POST"])
@api
def students(request, history, data, class_name):
    #添加学生（可以一次添加多个），内容为{"student_ids": [编号, ...]}
    #放在一个事务里：其中有一个不合格（重名、名称不合格、超过人数上限）时，一个都不添加
    student_ids = data.get("student_ids")
    if not isinstance(student_ids, list) or not student_ids:
        raise ValueError("student_ids must be a non-empty list")
    with history.transaction():
        for student_id in student_ids:
            history.add_student(class_name, text_of(student_id))
    return {"class": describe_class(history, class_name)}


@require_http_methods(["PUT", "DELETE"])
@api
def student_detail(request, history, data, class_name, student_id):
    #PUT：修改学生，内容可以包含{"student_id": 新编号}和{"active": 是否在班}。
    #DELETE：删除从未入座的学生
    if request.method == "DELETE":
        history.delete_student(class_name, student_id)
    else:
        if "active" in data and not isinstance(data["active"], bool):
            raise ValueError("active must be true or false")
        with history.transaction():
            if "active" in data:
                history.set_student_active(class_name, student_id, data["active"])
            if "student_id" in data:
                history.rename_student(class_name, student_id, text_of(data["student_id"]))
    return {"class": describe_class(history, class_name)}


@require_http_methods(["GET"])
@api
def student_history(request, history, data, class_name, student_id):
    #某个学生的历次座位
    track = history.get_student_history(class_name, student_id)
    return {"history": [dict(item, seat=list(item["seat"])) for item in track]}


@require_http_methods(["POST"])
@api
def preview(request, history, data, class_name):
    #生成一个新方案供预览：只计算，不应用、不保存，可以反复调用
    classroom, students, allocator = load_class(history, class_name)
    if not students:
        raise ValueError("Class has no students")
    assignment, plan_score = allocator.select_best_assignment()
    return describe_plan(assignment, plan_score, history.latest_assignment_id(class_name))


@require_http_methods(["POST"])
@api
def score(request, history, data, class_name):
    #给浏览器发来的方案打分（手动对调学生之后用），内容为{"plan": {student_id: [x, y]}, "base": 编号}
    #不应用、不保存
    check_unchanged(history, class_name, data.get("base"))
    classroom, students, allocator = load_class(history, class_name)
    assignment = plan_to_assignment(classroom, students, data.get("plan"))
    return describe_plan(assignment, allocator.score_assignment(assignment), data["base"])


@require_http_methods(["GET", "POST"])
@api
def assignments(request, history, data, class_name):
    if request.method == "POST":
        #确认方案：应用并保存，内容为{"plan": {student_id: [x, y]}, "base": 编号}
        #整个过程放在一个事务里：从检查数据是否变化到保存完成，中间不会被别的请求插进来改动数据
        with history.transaction():
            check_unchanged(history, class_name, data.get("base"))
            classroom, students, allocator = load_class(history, class_name)
            if not students:
                raise ValueError("Class has no students")
            assignment = plan_to_assignment(classroom, students, data.get("plan"))

            #分数由服务器重新计算，不采用浏览器传来的数值
            record = SeatAssignment(None)
            record.set_seat_assignment(assignment, allocator.score_assignment(assignment))   #先记录
            allocator.apply_assignment(assignment)                                            #再应用
            assignment_id = history.record_assignment(class_name, record)                     #最后保存
        return {"assignment_id": assignment_id, "class": describe_class(history, class_name)}

    #GET：最近的若干个历史方案，网址参数limit为数量（默认20，最多MAX_QUERY_LIMIT）。新的排在前面
    #多取一个，用来判断是否还有更早的方案
    try:
        limit = int(request.GET.get("limit", 20))
    except ValueError:
        raise ValueError("limit must be an integer") from None
    if not 1 <= limit <= MAX_QUERY_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_QUERY_LIMIT}")
    found = history.query_assignments(class_name=class_name, limit=limit + 1)
    has_more = len(found) > limit
    return {"assignments": list(reversed(found[-limit:])), "has_more": has_more}


@require_http_methods(["DELETE"])
@api
def latest_assignment(request, history, data, class_name):
    #撤销上一次排座：删除最近一次方案的记录。内容为{"assignment_id": 要撤销的方案编号}，
    #它必须正是当前最新的方案，否则说明数据已经变化（例如已经被撤销过），拒绝执行
    with history.transaction():
        check_unchanged(history, class_name, data.get("assignment_id"))
        assignment_id = history.rollback_last(class_name)
    return {"assignment_id": assignment_id, "class": describe_class(history, class_name)}


@require_http_methods(["GET"])
@api
def integrity(request, history, data):
    #检查数据完整性，返回发现的问题列表
    return {"problems": history.check_integrity()}


@require_http_methods(["GET"])
@api
def app_info(request, history, data):
    #程序信息：版本号、是否为桌面版、数据文件夹的位置。界面据此决定是否显示桌面版专用的按钮
    return {"version": seat_random.__version__, "desktop": settings.DESKTOP_MODE,
            "data_dir": str(settings.DATA_DIR) if settings.DESKTOP_MODE else ""}


@require_http_methods(["POST"])
@api
def app_open(request, history, data):
    #桌面版专用：用Windows打开数据文件夹或使用说明文件，内容为{"what": "data"或"help"}
    #只能打开这两个固定的位置，不接受浏览器传来的路径
    if not settings.DESKTOP_MODE:
        raise ValueError("Only available in the desktop app")
    targets = {"data": str(settings.DATA_DIR), "help": settings.HELP_FILE}
    target = targets.get(data.get("what"))
    if not target or not os.path.exists(target):
        raise ValueError(f"Cannot open: {data.get('what')}")
    os.startfile(target)
    return {"opened": data["what"]}
