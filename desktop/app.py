#桌面版的启动程序：双击后在程序自己的窗口里打开"座位随机重排"，不需要使用者输入网址或操作终端
#
#做的事情：
#  1. 确定数据文件夹的位置（便携版放在程序旁边，安装版放在用户的个人文件夹里）
#  2. 在本机启动网页服务（只有这台电脑自己能访问，端口由系统自动分配）
#  3. 打开程序窗口显示界面。窗口用Windows自带的WebView2组件显示网页（由pywebview这个工具包调用），
#     没有地址栏、标签页，也不带任何浏览器扩展或登录提示，看起来就是一个普通的程序
#  4. 窗口关闭后停止服务并退出
#
#界面本身就是web文件夹里的网页，所以桌面版和网页版的功能完全相同。
#直接运行（开发时，需要在打包环境randomseat-build中）：python desktop/app.py
#打包：python desktop/build.py。打包后，使用者双击的是一个很小的启动器（launcher/launcher.c），
#它用程序自带的Python运行本文件，并通过环境变量SEAT_APP_DIR告诉本文件程序文件夹的位置
import ctypes
import json
import logging
import logging.handlers
import os
import sys
import threading
import urllib.request
import winreg
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

APP_NAME = "座位随机重排"

#程序所在的文件夹：打包后是启动器所在的文件夹（由启动器通过环境变量SEAT_APP_DIR传入）；
#直接运行本文件时是desktop文件夹
PACKAGED = bool(os.environ.get("SEAT_APP_DIR"))
APP_DIR = Path(os.environ["SEAT_APP_DIR"]) if PACKAGED else Path(__file__).resolve().parent
if not PACKAGED:
    #直接运行时，要能找到项目根目录下的seat_random和web两个包
    sys.path.insert(0, str(APP_DIR.parent))
#窗口图标：打包后与本文件放在一起，直接运行时在assets文件夹里
ICON_FILE = Path(__file__).resolve().parent / "app.ico" if PACKAGED else APP_DIR / "assets" / "app.ico"

logger = logging.getLogger("desktop")


def find_data_dir():
    #数据文件夹的位置：
    #  便携版（程序旁边有portable.txt这个标记文件）：程序旁边的data文件夹，整个文件夹可以放在U盘里带走
    #  安装版：用户个人文件夹下的 AppData\Roaming\RandomSeat。程序装在哪里、是否重装都不影响数据
    #环境变量SEAT_DATA_DIR可以指定别的位置（测试时使用）
    if os.environ.get("SEAT_DATA_DIR"):
        return Path(os.environ["SEAT_DATA_DIR"])
    if (APP_DIR / "portable.txt").exists():
        return APP_DIR / "data"
    return Path(os.environ["APPDATA"]) / "RandomSeat"


def show_message(text, is_error=False):
    #弹出一个Windows消息框（程序没有终端窗口，出错时只能这样告诉使用者）
    ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10 if is_error else 0x40)


def setup_logging(data_dir):
    #运行日志写到数据文件夹下的logs\app.log，最多保留两个512KB的文件。出问题时可以把它发给开发者
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_dir / "app.log", maxBytes=512 * 1024, backupCount=1,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    #打包成没有终端窗口的程序后，标准输出和标准错误不存在；有些库会往里面写东西，这里把它们接到日志文件上
    if sys.stdout is None:
        sys.stdout = open(log_dir / "output.log", "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout


class Server(ThreadingMixIn, WSGIServer):
    #本机网页服务：每个请求用一个线程处理；程序退出时这些线程随之结束
    daemon_threads = True


class Handler(WSGIRequestHandler):
    def log_message(self, format, *args):
        #每个请求的访问记录只在调试时需要，不写进日志文件（避免日志很快变大）
        logger.debug(format, *args)


def start_server():
    #启动网页服务，返回(服务对象, 网址)。地址127.0.0.1表示只有本机能访问；端口0表示由系统挑一个空闲的端口
    import django
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.wsgi import get_wsgi_application

    django.setup()
    #StaticFilesHandler负责提供网页用到的脚本、样式和图标文件
    application = StaticFilesHandler(get_wsgi_application())
    server = make_server("127.0.0.1", 0, application, server_class=Server, handler_class=Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/"


def running_instance(data_dir):
    #程序是否已经在运行（使用者又双击了一次）。已经在运行时返回它的网址，否则返回None
    #运行中的程序会把自己的端口记在数据文件夹的running.json里；这里按记录访问一下，确认它真的还在
    try:
        port = json.loads((data_dir / "running.json").read_text(encoding="utf-8"))["port"]
        url = f"http://127.0.0.1:{port}/"
        with urllib.request.urlopen(url + "api/app", timeout=2) as response:
            if "version" in json.loads(response.read()):
                return url
    except (OSError, ValueError, KeyError, TypeError):
        #文件不存在、内容不对、端口上没有服务：都说明程序没有在运行
        return None
    return None


def find_app_window():
    #找到程序的主窗口，返回它的窗口句柄；没有找到返回None
    #不能只按标题查找：同一个程序里还有一个同名但看不见的辅助窗口。
    #这里逐个查看桌面上的窗口，要求标题相同、看得见、并且是用WinForms建立的（pywebview的窗口属于这一类）
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def check(window, _):
        title = ctypes.create_unicode_buffer(256)
        kind = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(ctypes.c_void_p(window), title, 256)
        user32.GetClassNameW(ctypes.c_void_p(window), kind, 256)
        if (title.value == APP_NAME and kind.value.startswith("WindowsForms")
                and user32.IsWindowVisible(ctypes.c_void_p(window))):
            found.append(window)
        return True         #继续查看下一个窗口

    user32.EnumWindows(check, None)
    return found[0] if found else None


def show_existing_window():
    #把已经打开的程序窗口显示到最前面（最小化了的先还原）。找到窗口返回True
    user32 = ctypes.windll.user32
    window = find_app_window()
    if window is None:
        return False
    if user32.IsIconic(ctypes.c_void_p(window)):
        user32.ShowWindow(ctypes.c_void_p(window), 9)        #9 = 还原窗口
    user32.SetForegroundWindow(ctypes.c_void_p(window))
    return True


def webview2_installed():
    #电脑上是否装有WebView2运行库（Windows 11自带；Windows 10一般已通过系统更新装上）
    #装了的话，注册表的下面三个位置之一会记着它的版本号
    key_path = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    places = [(winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + key_path),
              (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + key_path),
              (winreg.HKEY_CURRENT_USER, "SOFTWARE\\" + key_path)]
    for root, path in places:
        try:
            with winreg.OpenKey(root, path) as key:
                version = winreg.QueryValueEx(key, "pv")[0]
        except OSError:
            continue        #这个位置没有记录，看下一个
        if version and version != "0.0.0.0":
            return True
    return False


def run_window(url, profile):
    #打开程序窗口，直到窗口被关闭才返回
    import webview

    #测试时把窗口放到屏幕之外很远的位置，不打扰正在使用电脑的人（窗口本身是正常打开的）
    offscreen = -30000 if os.environ.get("SEAT_TEST_HIDDEN") == "1" else None
    webview.create_window(APP_NAME, url, width=1280, height=860, min_size=(960, 640),
                          x=offscreen, y=offscreen,
                          text_select=True)       #允许选中并复制界面上的文字
    #private_mode=False加上storage_path：界面的显示偏好保存在profile文件夹里，下次打开仍然有效
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(profile), icon=str(ICON_FILE))


def run_browser_fallback(url):
    #电脑上没有WebView2时：用默认浏览器打开界面，同时显示一个提示框；点提示框的"确定"即退出程序
    import webbrowser

    #测试这种方式时（SEAT_TEST_NO_WEBVIEW=1）不真的打开浏览器，免得在测试的电脑上弹出网页
    if os.environ.get("SEAT_TEST_NO_WEBVIEW") != "1":
        webbrowser.open(url)
    #提示框一直开着，程序就一直在运行；使用者点"确定"后这一行才结束
    show_message(f"{APP_NAME}正在运行，操作界面已经在浏览器中打开。\n\n"
                 "使用期间请不要关闭这个提示框。\n用完之后，再点下面的“确定”退出程序。")


def main():
    #告诉Windows这是一个独立的程序（而不是"Python"），任务栏上才会显示本程序自己的图标并单独成组
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RandomSeat.Desktop")

    #窗口组件的缓存和界面的显示偏好放在本机的 AppData\Local\RandomSeat\window 中：
    #它会慢慢变大，不放进数据文件夹，免得便携版的文件夹越用越大。测试时（指定了数据文件夹）放在数据文件夹里
    if os.environ.get("SEAT_DATA_DIR"):
        profile = Path(os.environ["SEAT_DATA_DIR"]) / "window"
    else:
        profile = Path(os.environ["LOCALAPPDATA"]) / "RandomSeat" / "window"

    data_dir = find_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(data_dir)

    #把这些位置告诉网页服务（web/settings.py从环境变量中读取）
    os.environ["SEAT_DATA_DIR"] = str(data_dir)
    os.environ["SEAT_DESKTOP"] = "1"
    os.environ["SEAT_HELP_FILE"] = str(APP_DIR / "使用说明.html")
    os.environ["DJANGO_SETTINGS_MODULE"] = "web.settings"

    #已经在运行：不再启动第二份，把已有的窗口显示到最前面，然后退出
    url = running_instance(data_dir)
    if url:
        logger.info("程序已经在运行：%s", url)
        if not show_existing_window():
            #找不到窗口（例如正在用浏览器显示界面）：在浏览器里再打开一次
            import webbrowser
            webbrowser.open(url)
        return

    server, url = start_server()
    use_window = webview2_installed() and os.environ.get("SEAT_TEST_NO_WEBVIEW") != "1"
    logger.info("服务已启动：%s，数据文件夹：%s，显示方式：%s", url, data_dir, "程序窗口" if use_window else "默认浏览器")
    running_file = data_dir / "running.json"
    running_file.write_text(json.dumps({"port": server.server_port, "pid": os.getpid()}), encoding="utf-8")
    try:
        if use_window:
            run_window(url, profile)
        else:
            run_browser_fallback(url)
    finally:
        running_file.unlink(missing_ok=True)
        server.shutdown()
        logger.info("程序退出")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        #启动失败时程序没有界面可以显示错误，这里弹出消息框并把完整的出错位置记入日志
        logging.getLogger("desktop").exception("程序启动失败")
        show_message(f"程序启动失败：\n{error}\n\n详细信息记录在数据文件夹的 logs\\app.log 中。", is_error=True)
        sys.exit(1)
