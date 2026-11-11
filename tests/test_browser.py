#网页界面的操作测试：启动网页服务和一个不显示窗口的Edge浏览器，像用户一样点击页面，检查结果
#用法：python tests/test_browser.py [截图保存的文件夹]
#  给出文件夹时，会在几个关键步骤把页面截图保存进去，便于人工查看界面
#
#说明：
#  - 需要本机装有Edge浏览器（路径见EDGE）。测试通过浏览器的调试接口控制页面，
#    环境中没有现成的工具包，所以下面用标准库写了一个最小的连接程序（CdpPage）
#  - 服务使用系统临时目录中的数据库，不会动项目data文件夹里的数据
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
SERVER_PORT = 8765
DEBUG_PORT = 9333
SITE = f"http://127.0.0.1:{SERVER_PORT}/"


class CdpPage:
    #通过浏览器的调试接口（WebSocket）控制一个页面：执行脚本、截图

    def __init__(self, websocket_url):
        #websocket_url形如 ws://127.0.0.1:9333/devtools/page/XXXX
        address, path = websocket_url[len("ws://"):].split("/", 1)
        host, port = address.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=60)
        handshake = (f"GET /{path} HTTP/1.1\r\nHost: {address}\r\nUpgrade: websocket\r\n"
                     f"Connection: Upgrade\r\nSec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\n"
                     "Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(handshake.encode())
        reply = b""
        while b"\r\n\r\n" not in reply:
            reply += self.sock.recv(1)
        assert b" 101 " in reply.split(b"\r\n")[0], reply
        self.next_id = 0
        self.errors = []          #页面脚本运行中出现的错误

    def _read(self, n):
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            assert chunk, "浏览器断开了连接"
            data += chunk
        return data

    def _send_text(self, text):
        #发送一条文本消息。按WebSocket的规定，客户端发出的内容要用4个随机字节做掩码
        payload = text.encode()
        mask = os.urandom(4)
        if len(payload) < 126:
            header = bytes([0x81, 0x80 | len(payload)])
        elif len(payload) < 65536:
            header = bytes([0x81, 0x80 | 126]) + struct.pack(">H", len(payload))
        else:
            header = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", len(payload))
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _receive_text(self):
        #接收一条完整的文本消息（可能分成多段发来）
        message = b""
        while True:
            first, second = self._read(2)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(length)
            opcode = first & 0x0F
            assert opcode in (0, 1), f"收到了预期之外的消息类型{opcode}"
            message += payload
            if first & 0x80:          #最后一段
                return message.decode()

    def command(self, method, **params):
        #发一条调试命令并等待它的结果；等待期间收到的页面错误通知记入self.errors
        self.next_id += 1
        self._send_text(json.dumps({"id": self.next_id, "method": method, "params": params}))
        while True:
            message = json.loads(self._receive_text())
            if message.get("method") == "Runtime.exceptionThrown":
                self.errors.append(message["params"]["exceptionDetails"])
            if message.get("id") == self.next_id:
                assert "error" not in message, message
                return message["result"]

    def js(self, expression):
        #在页面中执行一段脚本并返回结果；脚本返回Promise时等它完成
        result = self.command("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        assert "exceptionDetails" not in result, result["exceptionDetails"]
        return result["result"].get("value")

    def wait(self, condition, what):
        #等到页面中的条件成立（最多10秒）
        ok = self.js("new Promise((resolve) => { const start = Date.now(); const check = () => {"
                     f" let ok = false; try {{ ok = !!({condition}); }} catch (e) {{}}"
                     " if (ok) resolve(true); else if (Date.now() - start > 10000) resolve(false);"
                     " else setTimeout(check, 30); }; check(); })")
        assert ok, f"等待超时：{what}"

    def click(self, selector):
        #页面在上一个操作的请求返回之前不接受新的操作，所以先等它空闲
        self.wait("!busy", "页面空闲")
        assert self.js(f"(() => {{ const e = document.querySelector({json.dumps(selector)});"
                       " if (!e || e.disabled) return false; e.click(); return true; })()"), f"无法点击：{selector}"

    def answer(self, value):
        #回答页面上弹出的询问框，返回框里的提示文字
        #value为文字时填进输入框并点"确定"；为True时点"确定"；为False或None时点"取消"
        self.wait("document.getElementById('ask').open", "询问框出现")
        message = self.js("document.getElementById('ask-message').textContent")
        if isinstance(value, str):
            self.js(f"document.getElementById('ask-input').value = {json.dumps(value)}")
        self.click("#ask-ok" if value or isinstance(value, str) else "#ask-cancel")
        self.wait("!document.getElementById('ask').open", "询问框关闭")
        return message

    def mouse(self, kind, x, y, **more):
        #模拟真实的鼠标操作（按下、移动、松开、滚轮），坐标是页面窗口中的位置
        #kind取mousePressed、mouseMoved、mouseReleased、mouseWheel
        self.command("Input.dispatchMouseEvent", type=kind, x=x, y=y, **more)

    def center(self, selector):
        #一个页面元素中心点在窗口中的坐标
        return self.js(f"(() => {{ const r = document.querySelector({json.dumps(selector)}).getBoundingClientRect();"
                       " return [r.left + r.width / 2, r.top + r.height / 2]; })()")

    def screenshot(self, folder, name):
        if folder is not None:
            #captureBeyondViewport：页面比窗口长时，把整页都截下来
            data = self.command("Page.captureScreenshot", format="png", captureBeyondViewport=True)["data"]
            (folder / name).write_bytes(base64.b64decode(data))


def wait_for_http(url, what):
    #等到一个地址可以访问（最多20秒），返回读到的内容
    for _ in range(100):
        try:
            return urllib.request.urlopen(url, timeout=2).read()
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"{what}没有启动")


def close_browser(browser):
    #关闭测试用的浏览器。先通过调试接口让它自己退出，再结束进程作为保险。
    #只结束进程是不够的：电脑上正开着Edge时，我们启动的那个进程会把工作转交给后台的进程，
    #结束它并不能真正关掉浏览器，临时文件夹会因为文件被占用而清理失败
    try:
        info = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=3).read())
    except OSError:
        info = None         #调试端口已经连不上，说明浏览器已经退出
    if info is not None:
        CdpPage(info["webSocketDebuggerUrl"])._send_text(json.dumps({"id": 1, "method": "Browser.close"}))
        for _ in range(50):         #最多等10秒，直到调试端口关闭
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=1).close()
            except OSError:
                break
            time.sleep(0.2)
    browser.terminate()
    browser.wait(timeout=20)


def run_tests(page, shots):
    seat = lambda k: f"document.querySelector('[data-seat=\"{k}\"] .who').textContent"

    # ---- 第一次打开：欢迎页，说明四个步骤 ----
    page.wait("document.querySelector('.welcome')", "欢迎页")
    assert "还没有班级" in page.js("document.querySelector('.welcome').textContent")
    assert page.js("document.querySelectorAll('.steps li').length") == 4
    assert page.js("document.getElementById('class-delete').disabled")
    assert page.js("document.getElementById('guide')") is None
    #网页版（不是桌面版）：没有"打开数据文件夹"，页脚有版本号
    assert page.js("state.app.desktop") is False and page.js("document.getElementById('open-data').hidden")
    assert page.js("document.getElementById('version').textContent").startswith("版本 ")
    #窗口图标文件存在
    assert page.js("(async () => (await fetch(document.querySelector('link[rel=icon]').href)).status)()") == 200

    # ---- 帮助页：没有班级时也能打开 ----
    page.click("[data-tab=help]")
    assert page.js("document.querySelectorAll('.help h3').length") == 5
    assert "历史负担" in page.js("document.querySelector('.help').textContent")
    assert page.js("document.getElementById('open-help')") is None           #桌面版才有
    page.screenshot(shots, "9_帮助.png")
    page.click("[data-tab=arrange]")
    page.screenshot(shots, "0_欢迎页.png")

    # ---- 示例班级：一键建好座位表和名单，可以直接排座 ----
    page.click("#welcome-demo")
    page.wait("state.name === '示例班级' && state.info && state.info.seats.length === 36", "建立示例班级")
    assert page.js("state.info.students.length") == 32 and page.js("state.tab") == "arrange"
    #引导条：前两步已完成，当前是第三步
    assert page.js("document.querySelectorAll('.guide-step.done').length") == 2
    assert "生成并保存第一次排座" in page.js("document.querySelector('.guide-step.current').textContent")
    page.click("#generate")
    page.wait("state.preview", "生成预览")
    page.click("#confirm")
    page.wait("!busy && state.info.latest", "确认方案")
    assert page.js("document.getElementById('guide')") is None                #排过座之后引导条消失
    #再点一次"示例班级"的名字不会冲突；这里直接把它删掉（询问框里输入班级名称确认）
    page.click("#class-delete")
    assert "示例班级" in page.answer("示例班级")
    page.wait("state.classes.length === 0 && state.name === null", "删除示例班级")

    # ---- 新建班级（询问框）；引导条指向第一步 ----
    page.click("#welcome-new")
    assert "新班级的名称" in page.answer(" 演示班 ")
    page.wait("state.name === '演示班' && state.info", "新建班级")
    assert page.js("document.getElementById('class-select').value") == "演示班"
    assert "还没有设置座位表" in page.js("document.querySelector('.empty').textContent")
    assert "画座位表" in page.js("document.querySelector('.guide-step.current').textContent")
    #重名：出现红色提示，班级列表不变
    page.click("#class-new")
    page.answer("演示班")
    page.wait("document.querySelector('#toast.error')", "重名提示")
    assert "班级已存在" in page.js("document.getElementById('toast').textContent")
    assert page.js("state.classes.length") == 1
    #在询问框里点"取消"，或按Esc：什么都不发生
    page.click("#class-new")
    page.answer(None)
    page.click("#class-new")
    page.wait("document.getElementById('ask').open", "询问框出现")
    page.command("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27)
    page.command("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape", windowsVirtualKeyCode=27)
    page.wait("!document.getElementById('ask').open", "按Esc关闭询问框")
    assert page.js("state.classes.length") == 1 and not page.js("busy")
    #引导条上的按钮：直接跳到该做的那一页
    page.click("#guide-go")
    assert page.js("state.tab") == "layout" and page.js("document.getElementById('guide-go')") is None
    page.click("[data-tab=arrange]")

    # ---- 座位表：点选4排5列，再去掉一个角 ----
    page.click("[data-tab=layout]")
    assert page.js("document.getElementById('save-seats').disabled")
    for x in range(1, 5):
        for y in range(1, 6):
            page.click(f'[data-cell="{x},{y}"]')
    page.click('[data-cell="4,5"]')          #再点一次取消
    assert page.js("document.querySelectorAll('.cell.on').length") == 19
    assert "已选 19 个座位" in page.js("document.getElementById('seat-count').textContent")
    page.screenshot(shots, "1_座位表.png")
    page.click("#save-seats")
    page.wait("state.info.seats.length === 19 && !state.editor.dirty", "保存座位表")
    assert "添加名单" in page.js("document.querySelector('.guide-step.current').textContent")    #引导条进到第二步
    assert page.js("document.querySelectorAll('.cell.on').length") == 19

    # ---- 名单：一次添加16人 ----
    page.click("[data-tab=roster]")
    names = [f"学生{i:02d}" for i in range(1, 17)]
    page.js("(() => { const t = document.getElementById('names');"
            f" t.value = {json.dumps('，'.join(names[:8]) + chr(10) + chr(10).join(names[8:]))};"
            " t.dispatchEvent(new Event('input')); })()")
    page.click("#add-students")
    page.wait("state.info.students.length === 16", "添加学生")
    assert "生成并保存第一次排座" in page.js("document.querySelector('.guide-step.current').textContent")
    assert page.js("[...document.querySelectorAll('tbody tr')].map(r => r.dataset.student)") == names
    assert page.js("document.getElementById('names').value") == ""
    #重复添加：提示出错，名单不变
    page.js("(() => { const t = document.getElementById('names'); t.value = '学生03';"
            " t.dispatchEvent(new Event('input')); })()")
    page.click("#add-students")
    page.wait("document.querySelector('#toast.error')", "重复学生提示")
    assert "学生已存在：学生03" in page.js("document.getElementById('toast').textContent")
    assert page.js("state.info.students.length") == 16
    page.screenshot(shots, "2_名单.png")

    # ---- 排座页的座位图：自动缩放到完整显示 ----
    page.click("[data-tab=arrange]")
    assert page.js("document.getElementById('rollback').disabled")
    #座位图整个落在显示区域之内
    fits = ("(() => { const v = document.getElementById('viewport').getBoundingClientRect();"
            " const s = document.getElementById('stage').getBoundingClientRect();"
            " return s.left >= v.left && s.top >= v.top && s.right <= v.right + 0.5 && s.bottom <= v.bottom + 0.5; })()")
    assert page.js(fits) and page.js("state.view.auto")
    small_scale = page.js("state.view.scale")
    assert 1 < small_scale <= 1.5                       #座位少时适当放大，但不超过1.5倍

    #很大的座位表（15排24列）：自动缩小到能完整显示
    page.js("(async () => { await api('POST', 'classes', { name: '大教室' });"
            " const positions = []; for (let x = 1; x <= 15; x++) for (let y = 1; y <= 24; y++) positions.push([x, y]);"
            " await api('PUT', 'classes/' + encodeURIComponent('大教室') + '/seats', { positions });"
            " await api('POST', 'classes/' + encodeURIComponent('大教室') + '/students', { student_ids: ['甲', '乙'] });"
            " state.classes = (await api('GET', 'classes')).classes; await selectClass('大教室'); render(); })()")
    page.wait("document.querySelectorAll('.seat').length === 360", "打开大教室")
    assert page.js(fits) and page.js("state.view.scale") < 0.7
    page.screenshot(shots, "7_大座位表自动缩放.png")
    #窗口变小后重新适应
    big_scale = page.js("state.view.scale")
    page.command("Emulation.setDeviceMetricsOverride", width=800, height=600, deviceScaleFactor=1, mobile=False)
    page.wait(f"state.view.scale < {big_scale}", "窗口变小后重新缩放")
    assert page.js(fits)
    page.command("Emulation.setDeviceMetricsOverride", width=1280, height=860, deviceScaleFactor=1, mobile=False)
    page.wait(f"Math.abs(state.view.scale - {big_scale}) < 1e-6", "窗口恢复后重新缩放")
    page.js("(async () => { await api('DELETE', 'classes/' + encodeURIComponent('大教室'));"
            " state.classes = (await api('GET', 'classes')).classes; await selectClass('演示班'); render(); })()")
    page.wait("state.name === '演示班' && document.querySelectorAll('.seat').length === 19", "回到演示班")
    assert abs(page.js("state.view.scale") - small_scale) < 1e-6

    # ---- 缩放和拖动（用真实的鼠标操作） ----
    #按钮放大：比例变为1.25倍，不再是自动缩放
    page.click("#zoom-in")
    assert abs(page.js("state.view.scale") - small_scale * 1.25) < 1e-6 and not page.js("state.view.auto")
    assert page.js("document.getElementById('zoom-label').textContent") == f"{round(small_scale * 1.25 * 100)}%"
    page.click("#zoom-out")
    assert abs(page.js("state.view.scale") - small_scale) < 1e-6
    #滚轮：向上滚放大、向下滚缩小；鼠标所指的座位在缩放前后位置不变
    target = '[data-seat="2,2"]'
    cx, cy = page.center(target)
    page.mouse("mouseWheel", cx, cy, deltaX=0, deltaY=-120)
    page.wait(f"state.view.scale > {small_scale}", "滚轮放大")
    nx, ny = page.center(target)
    assert abs(nx - cx) < 1 and abs(ny - cy) < 1
    page.mouse("mouseWheel", cx, cy, deltaX=0, deltaY=120)
    page.mouse("mouseWheel", cx, cy, deltaX=0, deltaY=120)
    page.wait(f"state.view.scale < {small_scale}", "滚轮缩小")
    assert page.js("window.scrollY") == 0               #滚轮没有带动整个页面滚动
    #拖动：座位图跟着鼠标移动同样的距离
    x0, y0 = page.js("[state.view.x, state.view.y]")
    vx, vy = page.center("#viewport")
    page.mouse("mousePressed", vx, vy, button="left", buttons=1, clickCount=1)
    page.mouse("mouseMoved", vx + 30, vy + 20, button="left", buttons=1)
    page.mouse("mouseMoved", vx + 90, vy - 50, button="left", buttons=1)
    page.mouse("mouseReleased", vx + 90, vy - 50, button="left", buttons=0, clickCount=1)
    x1, y1 = page.js("[state.view.x, state.view.y]")
    assert abs(x1 - x0 - 90) < 1 and abs(y1 - y0 + 50) < 1
    assert not page.js("document.getElementById('viewport').classList.contains('dragging')")
    #"适应窗口"：回到自动缩放
    page.click("#zoom-fit")
    assert page.js("state.view.auto") and abs(page.js("state.view.scale") - small_scale) < 1e-6 and page.js(fits)

    # ---- 排座：生成预览 ----
    page.click("#generate")
    page.wait("state.preview", "生成预览")
    assert page.js("document.querySelectorAll('button.seat').length") == 19
    assert page.js("document.querySelectorAll('.seat.vacant').length") == 3
    assert page.js("document.getElementById('preview-score').textContent") == page.js("state.preview.score.toFixed(3)")
    assert page.js("state.info.latest") is None          #预览没有保存任何东西
    #橙色边框标出与现在不同的座位：第一次排座时是全部16个有人的座位，3个空座没有变化
    assert page.js("document.querySelectorAll('.seat.moved').length") == 16

    # ---- 预览中拖动座位图：从一个座位上按下并拖动，只移动画面，不会选中这个座位 ----
    sx, sy = page.center('[data-seat="2,2"]')
    x0 = page.js("state.view.x")
    page.mouse("mousePressed", sx, sy, button="left", buttons=1, clickCount=1)
    page.mouse("mouseMoved", sx + 40, sy, button="left", buttons=1)
    page.mouse("mouseReleased", sx + 40, sy, button="left", buttons=0, clickCount=1)
    assert abs(page.js("state.view.x") - x0 - 40) < 1 and page.js("state.picked") is None
    #不移动的普通点击（真实鼠标）：选中座位；再点一次取消
    #（稍等片刻再点：紧跟在拖动结束后5毫秒之内的点击会被当作拖动的一部分，人的操作不会这么快）
    time.sleep(0.1)
    sx, sy = page.center('[data-seat="2,2"]')
    for expected in ("2,2", None):
        page.mouse("mousePressed", sx, sy, button="left", buttons=1, clickCount=1)
        page.mouse("mouseReleased", sx, sy, button="left", buttons=0, clickCount=1)
        assert page.js("state.picked") == expected, (expected, page.js("state.picked"), page.js(
            "(() => { const e = document.elementFromPoint(%r, %r); return [e.tagName, e.className, e.dataset.seat || '']; })()" % (sx, sy)))
    page.click("#zoom-fit")

    # ---- 显示偏好：默认不显示座位负担、历史负担表格折叠 ----
    assert page.js("document.getElementById('burden-table')") is None
    assert page.js("document.querySelectorAll('.seat .sub').length") == 0
    assert page.js("document.querySelector('.legend')") is None
    assert page.js("document.querySelectorAll('.seat.plain').length") == 19
    assert "负担" not in page.js("document.querySelector('[data-seat=\"2,2\"]').title")
    wide = page.js("document.getElementById('viewport').clientWidth")
    #打开"显示座位负担"：每个座位出现负担值的数字和底色，下方出现图例
    page.click("#show-burden")
    assert page.js("document.querySelectorAll('.seat .sub').length") == 19
    assert page.js("document.querySelectorAll('.seat.plain').length") == 0
    assert page.js("document.querySelector('.legend') !== null && document.getElementById('show-burden').checked")
    assert page.js("document.querySelector('[data-seat=\"2,2\"]').style.background") != ""
    #展开历史负担表格：预览时有4列、16名学生；座位图的显示区域变窄，仍然完整显示
    page.click("#toggle-table")
    assert page.js("document.querySelectorAll('#burden-table tbody tr').length") == 16
    assert page.js("document.querySelectorAll('#burden-table thead th').length") == 4
    assert page.js("document.getElementById('viewport').clientWidth") < wide and page.js(fits)
    #偏好保存在浏览器里
    assert page.js("[localStorage.getItem('randomSeat.showBurden'), localStorage.getItem('randomSeat.showTable')]") == ["1", "1"]

    # ---- 手动对调：两个有人的座位 ----
    plan = page.js("previewPlan()")
    first, second = names[0], names[1]
    key_a, key_b = (",".join(map(str, plan[first])), ",".join(map(str, plan[second])))
    old_score = page.js("state.preview.score")
    page.click(f'[data-seat="{key_a}"]')
    assert page.js("document.querySelector('.seat.picked').dataset.seat") == key_a
    page.click(f'[data-seat="{key_b}"]')
    #对调动画进行中：两块名字牌正在移动，座位上原来的名字被隐藏，而方案和座位上的文字都还没有改
    assert page.js("[...document.querySelectorAll('.ghost')].map(g => g.textContent).sort()") == sorted([first, second])
    assert page.js("[...document.querySelectorAll('.ghost')].every(g => g.style.transform.startsWith('translate'))")
    hidden = "document.querySelector('[data-seat=\"{}\"] .who').style.visibility"
    assert page.js(hidden.format(key_a)) == "hidden" and page.js(hidden.format(key_b)) == "hidden"
    assert page.js(seat(key_a)) == first and page.js(seat(key_b)) == second
    assert page.js("previewPlan()") == plan and page.js("busy")
    page.wait(f"previewPlan()[{json.dumps(first)}].join(',') === '{key_b}' && state.picked === null", "对调")
    #动画结束：名字牌消失，座位上显示对调后的名字
    assert page.js("document.querySelectorAll('.ghost').length") == 0
    assert page.js(hidden.format(key_a)) == "" and page.js(hidden.format(key_b)) == ""
    assert page.js(seat(key_a)) == second and page.js(seat(key_b)) == first
    assert page.js("document.getElementById('preview-score').textContent") == page.js("state.preview.score.toFixed(3)")
    new_plan = page.js("previewPlan()")
    assert new_plan[second] == plan[first] and all(new_plan[n] == plan[n] for n in names[2:])
    #再次点同一个座位两次：选中后取消，方案不变
    page.click(f'[data-seat="{key_a}"]')
    page.click(f'[data-seat="{key_a}"]')
    assert page.js("state.picked") is None and page.js("previewPlan()") == new_plan

    # ---- 把学生移到空座位 ----
    empty = page.js("document.querySelector('.seat.vacant').dataset.seat")
    page.click(f'[data-seat="{key_b}"]')
    page.click(f'[data-seat="{empty}"]')
    #移到空座位：只有一块名字牌在移动
    assert page.js("[...document.querySelectorAll('.ghost')].map(g => g.textContent)") == [first]
    page.wait(f"previewPlan()[{json.dumps(first)}].join(',') === '{empty}'", "移到空座位")
    assert page.js("document.querySelectorAll('.ghost').length") == 0
    assert page.js(seat(empty)) == first and page.js(seat(key_b)) == "空座"
    del old_score
    page.screenshot(shots, "3_预览与对调.png")

    # ---- 放弃预览后重新生成，再调整一次并确认 ----
    page.click("#discard")
    assert page.js("state.preview") is None and page.js("document.querySelectorAll('.seat.vacant').length") == 19
    page.click("#generate")
    page.wait("state.preview", "重新生成")
    plan = page.js("previewPlan()")
    key_a, key_b = (",".join(map(str, plan[first])), ",".join(map(str, plan[second])))
    page.click(f'[data-seat="{key_a}"]')
    page.click(f'[data-seat="{key_b}"]')
    page.wait(f"previewPlan()[{json.dumps(first)}].join(',') === '{key_b}'", "对调")
    final_plan = page.js("previewPlan()")
    final_score = page.js("state.preview.score")
    page.click("#confirm")
    page.wait("state.info.latest && !state.preview", "确认方案")
    assert abs(page.js("state.info.latest.score") - final_score) < 1e-12
    #保存后页面显示的就是确认的方案，座位不再是可点击的按钮
    for name in names:
        assert page.js(seat(",".join(map(str, final_plan[name])))) == name
    assert page.js("document.querySelectorAll('button.seat').length") == 0
    assert not page.js("document.getElementById('rollback').disabled")
    assert page.js("document.getElementById('guide')") is None           #保存了第一次排座，引导条不再出现
    page.screenshot(shots, "4_确认后.png")

    saved_count = "(async () => (await api('GET', classPath('/assignments'))).assignments.length)()"
    assert page.js(saved_count) == 1

    # ---- 连点两次"确认并保存"：只保存一次 ----
    page.click("#generate")
    page.wait("state.preview", "生成预览")
    page.js("(() => { const b = document.getElementById('confirm'); b.click(); b.click(); b.click(); })()")
    page.wait("!busy && !state.preview && state.info.students[0].rounds === 2", "确认方案")
    assert page.js(saved_count) == 2

    # ---- 预览期间，另一个网页抢先排了一次座：确认被拒绝，页面自动刷新为最新数据 ----
    page.click("#generate")
    page.wait("state.preview", "生成预览")
    page.js("(async () => { const p = await api('POST', classPath('/preview')); const plan = {};"
            " for (const [id, item] of Object.entries(p.assignments)) plan[id] = item.current_seat;"
            " await api('POST', classPath('/assignments'), { plan, base: p.base }); })()")
    page.click("#confirm")
    page.wait("!busy && !state.preview && document.querySelector('#toast.error')", "过期的确认被拒绝")
    assert "数据已被其他操作修改" in page.js("document.getElementById('toast').textContent")
    assert page.js("state.info.students[0].rounds") == 3 and page.js(saved_count) == 3
    assert page.js("document.querySelectorAll('button.seat').length") == 0       #已退出预览

    # ---- 同样的情况下点对调：也被拒绝并刷新 ----
    page.click("#generate")
    page.wait("state.preview", "生成预览")
    stale_plan = page.js("previewPlan()")
    page.js("(async () => { await api('DELETE', classPath('/assignments/latest'),"
            " { assignment_id: state.info.latest.assignment_id }); })()")
    page.click(f'[data-seat="{",".join(map(str, stale_plan[first]))}"]')
    page.click(f'[data-seat="{",".join(map(str, stale_plan[second]))}"]')
    page.wait("!busy && !state.preview && document.querySelector('#toast.error')", "过期的对调被拒绝")
    assert page.js("state.info.students[0].rounds") == 2 and page.js(saved_count) == 2
    #页面以为的"最新方案"已被撤销时再点撤销：这里页面已经刷新过，所以撤销的是真正最新的一条；
    #先补回一轮，保持后面的步骤有3条记录
    page.click("#generate")
    page.wait("state.preview", "生成预览")
    page.click("#confirm")
    page.wait("!busy && !state.preview && state.info.students[0].rounds === 3", "确认方案")

    # ---- 服务连不上时的提示（把页面的网络请求临时换成必定失败的） ----
    page.js("window.realFetch = window.fetch; window.fetch = () => Promise.reject(new TypeError('Failed to fetch'));")
    page.click("#check")
    page.wait("document.getElementById('toast').textContent.includes('无法连接服务')", "连接失败提示")
    page.js("window.fetch = window.realFetch;")

    # ---- 名单文件的编码：UTF-8（带或不带文件头标记）和GBK都能读成正确的文字 ----
    assert page.js("decodeText(new Uint8Array([0xE5, 0xBC, 0xA0, 0xE4, 0xB8, 0x89]).buffer)") == "张三"
    assert page.js("decodeText(new Uint8Array([0xEF, 0xBB, 0xBF, 0xE5, 0xBC, 0xA0, 0xE4, 0xB8, 0x89]).buffer)") == "张三"
    assert page.js("decodeText(new Uint8Array([0xD5, 0xC5, 0xC8, 0xFD, 0x0D, 0x0A, 0xC0, 0xEE, 0xCB, 0xC4]).buffer)") == "张三\r\n李四"

    # ---- 刷新网页后回到同一个班级、同一个页面，数据还在 ----
    latest_id = page.js("state.info.latest.assignment_id")
    page.command("Page.reload")
    page.wait("typeof state !== 'undefined' && state.info && state.name === '演示班'", "刷新网页")
    assert page.js("state.tab") == "arrange" and page.js("state.info.latest.assignment_id") == latest_id
    #显示偏好在刷新后仍然有效；然后都关掉，回到默认的样子
    assert page.js("state.showBurden && state.showTable")
    assert page.js("document.querySelectorAll('.seat .sub').length") == 19
    assert page.js("document.querySelectorAll('#burden-table thead th').length") == 2      #不在预览中：只有2列
    page.click("#show-burden")
    page.click("#toggle-table")
    assert page.js("document.getElementById('burden-table')") is None
    assert page.js("document.querySelectorAll('.seat .sub').length") == 0
    page.screenshot(shots, "8_默认显示.png")

    # ---- 历史：3条记录，点选最早的一条 ----
    page.click("[data-tab=history]")
    page.wait("state.history && document.querySelectorAll('tr.clickable').length === 3", "读取历史")
    assert page.js("Number(document.querySelector('tr.selected').dataset.assignment)") == latest_id
    page.click("tr.clickable:last-child")
    oldest = page.js("state.shown")
    assert oldest < latest_id
    shown_names = page.js("[...document.querySelectorAll('.seat:not(.vacant) .who')].map(e => e.textContent).sort()")
    assert shown_names == names
    #最早的记录就是第一次确认的方案
    assert page.js("state.history.assignments.find(a => a.assignment_id === state.shown).assignments"
                   f"[{json.dumps(first)}].current_seat") == final_plan[first]
    page.screenshot(shots, "5_历史.png")

    # ---- 名单：查看学生历史、改名、标记离开 ----
    page.click("[data-tab=roster]")
    page.click(f'tr[data-student="{first}"] .actions button:nth-child(1)')
    page.wait("document.getElementById('dialog').open", "学生历史弹窗")
    assert page.js("document.querySelectorAll('#dialog tbody tr').length") == 3
    page.screenshot(shots, "6_学生历史.png")
    page.click("#dialog button")
    assert not page.js("document.getElementById('dialog').open")

    page.click(f'tr[data-student="{first}"] .actions button:nth-child(2)')
    #询问框里预先填着原来的名字
    page.wait("document.getElementById('ask').open", "询问框出现")
    assert page.js("document.getElementById('ask-input').value") == first
    page.answer("张三")
    page.wait("state.info.students[0].student_id === '张三'", "学生改名")
    assert page.js("state.info.students[0].rounds") == 3
    page.click('tr[data-student="张三"] .actions button:nth-child(3)')
    page.wait("state.info.students[0].active === false", "标记离开")
    assert page.js("document.querySelector('tr[data-student=\"张三\"]').className") == "inactive"
    #有历史的学生，删除按钮不可用
    assert page.js("document.querySelector('tr[data-student=\"张三\"] .actions button:nth-child(4)').disabled")
    #新加一个从未入座的学生，可以删除（确认框回答"是"）
    page.js("(() => { const t = document.getElementById('names'); t.value = '录错的';"
            " t.dispatchEvent(new Event('input')); })()")
    page.click("#add-students")
    page.wait("state.info.students.length === 17", "添加学生")
    #中途加入的学生：历史负担从全班在班学生当前的平均值开始（不是0）
    assert page.js("(() => { const s = state.info.students; const others = s.filter(x => x.active && x.rounds);"
                   " const mean = others.reduce((t, x) => t + x.accum_burden, 0) / others.length;"
                   " return Math.abs(s[16].accum_burden - mean) < 1e-9 && Math.abs(mean) > 1e-6; })()")
    #名称里的多余空格被整理成一个；之后只差全角空格的名字算重名，被拒绝
    page.js("(() => { const t = document.getElementById('names'); t.value = ' 王   五 ';"
            " t.dispatchEvent(new Event('input')); })()")
    page.click("#add-students")
    page.wait("state.info.students.length === 18 && state.info.students[17].student_id === '王 五'", "整理空格")
    page.js("(() => { const t = document.getElementById('names'); t.value = '王\\u3000五';"
            " t.dispatchEvent(new Event('input')); })()")
    page.click("#add-students")
    page.wait("document.getElementById('toast').textContent.includes('学生已存在：王 五')", "整理空格后重名")
    assert page.js("state.info.students.length") == 18
    page.js("(() => { document.getElementById('names').value = ''; state.draft = ''; })()")
    page.click('tr[data-student="王 五"] .actions button:nth-child(4)')
    assert "王 五" in page.answer(False)                 #先点"取消"：不删除
    assert page.js("state.info.students.length") == 18
    page.click('tr[data-student="王 五"] .actions button:nth-child(4)')
    page.answer(True)
    page.wait("state.info.students.length === 17", "删除学生")
    page.click('tr[data-student="录错的"] .actions button:nth-child(4)')
    page.answer(True)
    page.wait("state.info.students.length === 16", "删除学生")

    # ---- 排座页：离开的学生不在座位上；撤销（先回答"否"，再回答"是"） ----
    page.click("[data-tab=arrange]")
    assert page.js("document.querySelectorAll('.seat.vacant').length") == 4
    page.click("#rollback")
    assert "撤销最近一次排座" in page.answer(False)
    assert page.js("state.info.latest.assignment_id") == latest_id
    page.click("#rollback")
    page.answer(True)
    page.wait(f"state.info.latest.assignment_id < {latest_id}", "撤销")
    assert page.js("state.info.students[1].rounds") == 2

    # ---- 座位表：去掉有人坐的座位时要先确认 ----
    page.click("[data-tab=layout]")
    occupied = page.js("state.info.students[1].current_seat.join(',')")
    page.click(f'[data-cell="{occupied}"]')
    page.click("#save-seats")
    assert "1 名学生" in page.answer(False)
    assert page.js("state.info.seats.length") == 19
    page.click("#save-seats")
    page.answer(True)
    page.wait("state.info.seats.length === 18", "保存座位表")
    assert page.js("state.info.students[1].current_seat") is None
    assert page.js("state.info.students[1].rounds") == 2          #历史保留

    # ---- 班级：改名；删除时名称输错不删除，输对才删除 ----
    page.click("#class-rename")
    page.answer("高一3班")
    page.wait("state.name === '高一3班'", "班级改名")
    assert page.js("state.classes") == ["高一3班"] and page.js("state.info.seats.length") == 18
    page.click("#check")
    page.wait("document.getElementById('toast').textContent.includes('数据完整')", "完整性检查")
    page.click("#class-delete")
    page.answer("演示班")
    page.wait("document.getElementById('toast').textContent.includes('输入的名称不一致')", "删除确认不一致")
    assert page.js("state.classes") == ["高一3班"]
    page.click("#class-delete")
    page.answer("高一3班")
    page.wait("state.classes.length === 0 && state.name === null", "删除班级")
    assert "还没有班级" in page.js("document.querySelector('.welcome').textContent")

    #整个过程中页面脚本没有出过错
    assert page.errors == [], page.errors


if __name__ == "__main__":
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if shots is not None:
        shots.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp:
        environment = dict(os.environ, SEAT_DB_PATH=str(Path(temp) / "browser_test.db"))
        server = subprocess.Popen(
            [sys.executable, "manage.py", "runserver", f"127.0.0.1:{SERVER_PORT}", "--noreload"],
            cwd=PROJECT_DIR, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = subprocess.Popen(
            [EDGE, "--headless=new", f"--remote-debugging-port={DEBUG_PORT}", f"--user-data-dir={Path(temp) / 'edge'}",
             "--no-first-run", "--window-size=1280,900", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_for_http(SITE, "网页服务")
            targets = json.loads(wait_for_http(f"http://127.0.0.1:{DEBUG_PORT}/json/list", "浏览器"))
            page = CdpPage(next(t for t in targets if t["type"] == "page")["webSocketDebuggerUrl"])
            page.command("Runtime.enable")
            page.command("Page.enable")
            #把页面宽度固定为常见的电脑屏幕宽度，截图才是正常使用时的样子
            page.command("Emulation.setDeviceMetricsOverride", width=1280, height=860, deviceScaleFactor=1, mobile=False)
            page.command("Page.navigate", url=SITE)
            page.wait("typeof state !== 'undefined' && document.querySelector('.welcome')", "打开网页")
            run_tests(page, shots)
            page.sock.close()
        finally:
            #无论测试是否通过，都要关掉服务和浏览器
            close_browser(browser)
            server.terminate()
            server.wait(timeout=20)
            time.sleep(1)       #等浏览器的子进程放开临时目录中的文件
    print("界面操作测试通过")
    print("全部通过")
