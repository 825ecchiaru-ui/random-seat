#发布包的测试：对desktop/dist里打包好的便携版zip和安装包做一遍实际的解压/安装、运行、卸载
#用法（先运行desktop/build.py生成发布包）：python desktop/test_release.py
#
#测试内容：
#  1. 数据文件夹位置的规则（便携版在程序旁边，安装版在个人文件夹）
#  2. 便携版：解压zip，运行其中的exe，确认数据保存在程序旁边的data文件夹
#  3. 没有WebView2组件时的备用方式：出现小窗口，服务可用，关闭小窗口后程序退出
#  4. 安装包：静默安装到临时文件夹，检查文件、快捷方式和登记信息，运行，再静默卸载并确认清理干净
#
#注意：第4项会在当前用户的桌面和开始菜单短暂出现快捷方式，测试结束时由卸载程序删除。
#如果这台电脑上已经正式安装了本程序，第4项会自动跳过，以免影响正式安装。
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import winreg
import zipfile
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parent
DIST_DIR = DESKTOP_DIR / "dist"
sys.path.insert(0, str(DESKTOP_DIR))
import test_app
import app as desktop_app
import seat_random

VERSION = seat_random.__version__
APP_NAME = "座位随机重排"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\RandomSeat"


def test_data_dir_rule():
    #不指定数据文件夹时：程序旁边有portable.txt则用旁边的data文件夹，否则用个人文件夹下的AppData\Roaming\RandomSeat
    saved_dir, saved_env = desktop_app.APP_DIR, os.environ.pop("SEAT_DATA_DIR", None)
    try:
        with tempfile.TemporaryDirectory() as temp:
            desktop_app.APP_DIR = Path(temp)
            assert desktop_app.find_data_dir() == Path(os.environ["APPDATA"]) / "RandomSeat"
            (Path(temp) / "portable.txt").write_text("x", encoding="utf-8")
            assert desktop_app.find_data_dir() == Path(temp) / "data"
            os.environ["SEAT_DATA_DIR"] = str(Path(temp) / "别处")
            assert desktop_app.find_data_dir() == Path(temp) / "别处"
    finally:
        desktop_app.APP_DIR = saved_dir
        os.environ.pop("SEAT_DATA_DIR", None)
        if saved_env is not None:
            os.environ["SEAT_DATA_DIR"] = saved_env
    print("数据文件夹位置规则测试通过")


def test_portable():
    archive = DIST_DIR / f"{APP_NAME}-便携版-{VERSION}.zip"
    with tempfile.TemporaryDirectory() as temp:
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            z.extractall(temp)
        folder = Path(temp) / f"{APP_NAME}-便携版-{VERSION}"
        #压缩包里只有一个顶层文件夹，里面有程序、说明和便携版标记
        assert all(name.startswith(folder.name + "/") for name in names)
        for name in (f"{APP_NAME}.exe", "portable.txt", "使用说明.html", "使用说明.pdf", "_internal",
                     "_internal/pythonw.exe", "_internal/app/app.py"):
            assert (folder / name).exists(), name
        assert not (folder / "data").exists()

        #不指定数据文件夹，直接运行：数据应当保存在程序旁边的data文件夹
        environment = {k: v for k, v in os.environ.items() if k != "SEAT_DATA_DIR"}
        environment.update(SEAT_TEST_HIDDEN="1",
                           WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={test_app.DEBUG_PORT}")
        program = subprocess.Popen([str(folder / f"{APP_NAME}.exe")], env=environment)
        try:
            page = test_app.connect()
            assert page.js("state.app.desktop") is True and page.js("state.app.version") == VERSION
            assert page.js("state.app.data_dir") == str(folder / "data")
            page.click("#welcome-demo")
            page.wait("state.name === '示例班级' && state.info && state.info.seats.length === 36", "建立示例班级")
            assert (folder / "data" / "seat_history.db").exists()
            page.sock.close()
            test_app.close_window()
            assert program.wait(timeout=60) == 0
        finally:
            test_app.kill_tree(program)
            time.sleep(1.5)
    print("便携版测试通过")


def test_fallback():
    #模拟电脑上没有WebView2组件：程序改为显示一个提示框（并用默认浏览器打开界面；测试时不真的打开浏览器）
    program_dir = DESKTOP_DIR / "build" / "files"
    with tempfile.TemporaryDirectory() as temp:
        data_dir = Path(temp) / "data"
        environment = dict(os.environ, SEAT_DATA_DIR=str(data_dir), SEAT_TEST_NO_WEBVIEW="1")
        program = subprocess.Popen([str(program_dir / f"{APP_NAME}.exe")], env=environment)
        try:
            #等服务启动，确认可以访问
            for _ in range(150):
                if (data_dir / "running.json").exists():
                    break
                time.sleep(0.2)
            port = json.loads((data_dir / "running.json").read_text(encoding="utf-8"))["port"]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/app", timeout=10) as response:
                assert json.loads(response.read())["desktop"] is True
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as response:
                assert "座位随机重排" in response.read().decode("utf-8")

            #提示框出现了（标题是程序名称；"#32770"是Windows提示框的窗口类型）；关闭它，程序退出
            user32 = ctypes.windll.user32
            window = None
            for _ in range(100):
                window = user32.FindWindowW("#32770", APP_NAME)
                if window:
                    break
                time.sleep(0.2)
            assert window, "没有出现备用方式的提示框"
            user32.PostMessageW(window, 0x0010, 0, 0)       #0x0010 = WM_CLOSE
            assert program.wait(timeout=60) == 0
            assert not (data_dir / "running.json").exists()
            assert "显示方式：默认浏览器" in (data_dir / "logs" / "app.log").read_text(encoding="utf-8")
        finally:
            test_app.kill_tree(program)
            time.sleep(1)
    print("没有WebView2时的备用方式测试通过")


def registry_value(path, name):
    #读取当前用户注册表里的一个值，不存在时返回None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def test_installer():
    if registry_value(UNINSTALL_KEY, "DisplayName") is not None:
        print("这台电脑上已经安装了本程序，跳过安装包测试（以免影响正式安装）")
        return

    installer = DIST_DIR / f"{APP_NAME}-安装包-{VERSION}.exe"
    desktop = Path(os.environ["USERPROFILE"]) / "Desktop" / f"{APP_NAME}.lnk"
    start_menu = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs" / APP_NAME
    #桌面文件夹可能被系统改到别的位置（例如OneDrive），这时以登记的位置为准
    shell_desktop = registry_value(r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders", "Desktop")
    if shell_desktop:
        desktop = Path(os.path.expandvars(shell_desktop)) / f"{APP_NAME}.lnk"

    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "安装 位置"
        #静默安装：/S 不显示界面，/D= 指定安装位置（必须是最后一个参数，且不加引号）
        subprocess.run(f'"{installer}" /S /D={target}', check=True, timeout=300)
        try:
            for name in (f"{APP_NAME}.exe", "使用说明.html", "使用说明.pdf", "卸载.exe", "_internal"):
                assert (target / name).exists(), name
            assert not (target / "portable.txt").exists()                  #安装版没有便携版标记
            assert desktop.exists(), desktop
            assert {p.name for p in start_menu.iterdir()} == {f"{APP_NAME}.lnk", "使用说明.lnk", f"卸载 {APP_NAME}.lnk"}
            assert registry_value(UNINSTALL_KEY, "DisplayName") == APP_NAME
            assert registry_value(UNINSTALL_KEY, "DisplayVersion") == VERSION
            assert registry_value(UNINSTALL_KEY, "InstallLocation") == str(target)
            assert registry_value(r"Software\RandomSeat", "InstallDir") == str(target)

            #运行安装好的程序（数据放到临时文件夹，不动个人文件夹里的正式数据）
            test_app.run([str(target / f"{APP_NAME}.exe")])

            #覆盖安装（相当于升级）：使用者放在安装文件夹里的其他文件不受影响
            (target / "我的笔记.txt").write_text("不要删", encoding="utf-8")
            subprocess.run(f'"{installer}" /S /D={target}', check=True, timeout=300)
            assert (target / f"{APP_NAME}.exe").exists() and (target / "我的笔记.txt").exists()
        finally:
            #静默卸载。_?= 让卸载程序原地运行并等它结束（否则它会把自己复制到别处后立刻返回）
            subprocess.run(f'"{target / "卸载.exe"}" /S _?={target}', check=True, timeout=300)

        #卸载后：程序文件、快捷方式、登记信息都没有了；使用者自己的文件还在
        left = sorted(p.name for p in target.iterdir())
        assert left == ["卸载.exe", "我的笔记.txt"], left         #原地运行的卸载程序无法删除自己，正常卸载时不会留下
        assert not desktop.exists() and not start_menu.exists()
        assert registry_value(UNINSTALL_KEY, "DisplayName") is None
        assert registry_value(r"Software\RandomSeat", "InstallDir") is None
    print("安装包测试通过（安装、运行、覆盖安装、卸载）")


if __name__ == "__main__":
    test_data_dir_rule()
    test_portable()
    test_fallback()
    test_installer()
    print("全部通过")
