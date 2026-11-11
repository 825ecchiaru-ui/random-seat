#桌面版启动程序的测试：启动程序、通过调试接口操作它打开的窗口、关闭窗口后确认程序自己退出
#用法：
#  python desktop/test_app.py                      测试直接运行的 desktop/app.py
#  python desktop/test_app.py 某个文件夹\座位随机重排.exe    测试打包好的程序
#测试使用系统临时目录中的数据文件夹，不会动正式数据；窗口不显示在屏幕上
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DESKTOP_DIR.parent / "tests"))
from test_browser import CdpPage, wait_for_http      #复用界面测试里控制浏览器的工具
sys.path.insert(0, str(DESKTOP_DIR))
import app as desktop_app                            #只用其中查找程序窗口的函数，不会启动程序

DEBUG_PORT = 9555


def launch(command, data_dir):
    #WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS是WebView2组件自己认识的环境变量，这里用它打开调试端口，
    #测试脚本才能操作程序窗口里的界面。SEAT_TEST_HIDDEN让窗口不显示到屏幕上
    environment = dict(os.environ, SEAT_DATA_DIR=str(data_dir), SEAT_TEST_HIDDEN="1",
                       WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={DEBUG_PORT}")
    return subprocess.Popen(command, env=environment)


def connect():
    #窗口刚打开时是一张空白页，随后才转到界面的网址；等它转过去之后再连接
    for _ in range(150):
        targets = json.loads(wait_for_http(f"http://127.0.0.1:{DEBUG_PORT}/json/list", "程序窗口"))
        pages = [t for t in targets if t["type"] == "page" and t["url"].startswith("http://127.0.0.1")]
        if pages:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("程序窗口没有打开界面")
    time.sleep(0.5)
    page = CdpPage(pages[0]["webSocketDebuggerUrl"])
    page.command("Runtime.enable")
    page.wait("typeof state !== 'undefined' && state.app.version && document.querySelector('.welcome, .seat, .empty')",
              "界面加载完成")
    return page


def kill_tree(process):
    #测试失败时结束还没有退出的程序。打包后的程序是"启动器 -> 自带的Python"两层进程，要连同子进程一起结束
    if process.poll() is None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)


def close_window():
    #像使用者点右上角的"×"一样关闭程序窗口（向窗口发送Windows的"关闭"消息）
    import ctypes
    window = desktop_app.find_app_window()
    assert window is not None, "找不到程序窗口"
    ctypes.windll.user32.PostMessageW(ctypes.c_void_p(window), 0x0010, 0, 0)      #0x0010 = WM_CLOSE


def run(command):
    with tempfile.TemporaryDirectory() as temp:
        data_dir = Path(temp) / "数据 文件夹"         #路径里故意带上中文和空格
        app = launch(command, data_dir)
        try:
            page = connect()

            #界面是桌面版：有"打开数据文件夹"，帮助页有"打开完整的使用说明"，显示数据文件夹的位置
            assert page.js("state.app.desktop") is True
            assert page.js("state.app.data_dir") == str(data_dir)
            assert not page.js("document.getElementById('open-data').hidden")
            assert page.js("document.title") == "座位随机重排"
            assert page.js("location.hostname") == "127.0.0.1"
            page.click("[data-tab=help]")
            assert page.js("document.getElementById('open-help') !== null")
            assert str(data_dir) in page.js("document.querySelector('.help').textContent")
            page.click("[data-tab=arrange]")

            #脚本、样式、图标都能读到（打包后它们在程序自带的文件夹里）
            for selector, attribute in (("script[src]", "src"), ("link[rel=stylesheet]", "href"), ("link[rel=icon]", "href")):
                status = page.js(f"(async () => (await fetch(document.querySelector('{selector}').{attribute})).status)()")
                assert status == 200, (selector, status)

            #完整走一遍：示例班级 -> 生成 -> 对调 -> 确认
            page.click("#welcome-demo")
            page.wait("state.name === '示例班级' && state.info && state.info.seats.length === 36", "建立示例班级")
            page.click("#generate")
            page.wait("state.preview", "生成预览")
            plan = page.js("previewPlan()")
            a, b = (",".join(map(str, plan[name])) for name in ("同学01", "同学02"))
            page.click(f'[data-seat="{a}"]')
            page.click(f'[data-seat="{b}"]')
            page.wait(f"!busy && previewPlan()['同学01'].join(',') === '{b}'", "对调")
            page.click("#confirm")
            page.wait("!busy && state.info.latest", "确认方案")
            saved = page.js("state.info.latest.assignment_id")
            page.click("#check")
            page.wait("document.getElementById('toast').textContent.includes('数据完整')", "完整性检查")

            #数据文件夹里有数据库、日志和"正在运行"的记录
            assert (data_dir / "seat_history.db").exists() and (data_dir / "logs" / "app.log").exists()
            port = json.loads((data_dir / "running.json").read_text(encoding="utf-8"))["port"]
            assert page.js("Number(location.port)") == port

            #程序已经在运行时再启动一次：不会再开一份服务，第二个启动程序很快自己退出（它只负责把已有的窗口显示到前面）
            second = launch(command, data_dir)
            assert second.wait(timeout=60) == 0
            assert json.loads((data_dir / "running.json").read_text(encoding="utf-8"))["port"] == port
            assert page.js("state.info.latest.assignment_id") == saved
            assert page.errors == [], page.errors

            #关闭窗口：程序自己退出，"正在运行"的记录被清除
            close_window()
            assert app.wait(timeout=60) == 0
            assert not (data_dir / "running.json").exists()

            #再次启动：上次的数据还在
            app = launch(command, data_dir)
            page = connect()
            page.wait("state.name === '示例班级' && state.info && state.info.latest", "重新打开后读到数据")
            assert page.js("state.info.latest.assignment_id") == saved
            close_window()
            assert app.wait(timeout=60) == 0
            log = (data_dir / "logs" / "app.log").read_text(encoding="utf-8")
            assert log.count("服务已启动") == 2 and log.count("程序已经在运行") == 1 and "ERROR" not in log, log
        finally:
            kill_tree(app)
            time.sleep(1.5)       #等浏览器的子进程放开临时目录中的文件
    print("桌面版启动程序测试通过")
    print("全部通过")


if __name__ == "__main__":
    #直接运行desktop/app.py需要pywebview，它装在打包环境里，所以这个测试要用打包环境的Python运行
    run([sys.argv[1]] if len(sys.argv) > 1 else [sys.executable, str(DESKTOP_DIR / "app.py")])
