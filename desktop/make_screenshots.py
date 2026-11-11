#生成使用说明里的界面截图，保存到desktop/guide/shots
#用法：python desktop/make_screenshots.py     （用项目的randomseatenv环境运行，需要本机装有Edge）
#做法与界面测试相同：启动网页服务和一个不显示窗口的Edge，自动操作一遍示例班级，在各个步骤截图。
#使用临时数据库，不会动正式数据。界面有改动后重新运行一次，再重新打包即可更新使用说明里的图
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = DESKTOP_DIR.parent
SHOTS_DIR = DESKTOP_DIR / "guide" / "shots"
sys.path.insert(0, str(PROJECT_DIR / "tests"))
import test_browser as tb       #复用界面测试里控制浏览器的工具


def take_screenshots(page):
    shot = lambda name: page.screenshot(SHOTS_DIR, name)

    page.wait("document.querySelector('.welcome')", "欢迎页")
    shot("01_welcome.png")

    #新建班级的询问框
    page.click("#welcome-new")
    page.wait("document.getElementById('ask').open", "询问框")
    page.js("document.getElementById('ask-input').value = '高一3班'")
    shot("02_new_class.png")
    page.answer("高一3班")
    page.wait("state.name === '高一3班' && state.info", "新建班级")

    #座位表：点出6排、每排3组双人座
    page.click("[data-tab=layout]")
    for x in range(1, 7):
        for y in (1, 2, 4, 5, 7, 8):
            page.click(f'[data-cell="{x},{y}"]')
    shot("03_layout.png")
    page.click("#save-seats")
    page.wait("state.info.seats.length === 36 && !state.editor.dirty", "保存座位表")

    #名单：先截一张正在输入的，再截一张添加之后的
    page.click("[data-tab=roster]")
    names = ["王芳", "李娜", "张伟", "刘洋", "陈静", "杨磊", "赵敏", "黄涛", "周杰", "吴倩", "徐明", "孙丽",
             "马超", "朱琳", "胡军", "郭燕", "何平", "高翔", "林雪", "罗毅", "梁晨", "宋佳", "郑浩", "谢婷",
             "韩冰", "唐宇", "冯岚", "于洋", "董慧", "萧然", "程诚", "曹颖"]
    page.js("(() => { const t = document.getElementById('names');"
            f" t.value = {json.dumps(chr(10).join(names[:6]) + chr(10) + '……')}; t.dispatchEvent(new Event('input')); }})()")
    shot("04_roster_input.png")
    page.js("(() => { const t = document.getElementById('names');"
            f" t.value = {json.dumps(chr(10).join(names))}; t.dispatchEvent(new Event('input')); }})()")
    page.click("#add-students")
    page.wait("state.info.students.length === 32", "添加学生")
    shot("05_roster.png")

    #排座：预览（选中一个座位，表示正在对调）
    page.click("[data-tab=arrange]")
    page.wait("!document.getElementById('toast').classList.contains('show')", "提示条消失")
    page.click("#generate")
    page.wait("state.preview", "生成预览")
    page.click('[data-seat="3,4"]')
    shot("06_preview.png")
    page.click('[data-seat="3,4"]')
    page.click("#confirm")
    page.wait("!busy && state.info.latest", "确认方案")
    page.wait("!document.getElementById('toast').classList.contains('show')", "提示条消失")
    shot("07_saved.png")

    #再排两次，之后打开"显示座位负担"和历史负担表
    for rounds in (2, 3):
        page.click("#generate")
        page.wait("state.preview", "生成预览")
        page.click("#confirm")
        page.wait(f"!busy && !state.preview && state.info.students[0].rounds === {rounds}", "确认方案")
    page.click("#show-burden")
    page.click("#toggle-table")
    page.wait("!document.getElementById('toast').classList.contains('show')", "提示条消失")
    shot("08_burden.png")
    page.click("#show-burden")
    page.click("#toggle-table")

    #历史
    page.click("[data-tab=history]")
    page.wait("state.history && document.querySelectorAll('tr.clickable').length === 3", "读取历史")
    shot("09_history.png")

    #名单里某个学生的座位历史
    page.click("[data-tab=roster]")
    page.click('tr[data-student="王芳"] .actions button:nth-child(1)')
    page.wait("document.getElementById('dialog').open", "学生历史弹窗")
    shot("10_student_history.png")
    page.click("#dialog button")

    #帮助页
    page.click("[data-tab=help]")
    shot("11_help.png")


if __name__ == "__main__":
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    random.seed(2026)
    with tempfile.TemporaryDirectory() as temp:
        #SEAT_DESKTOP=1：截图里的界面与桌面版一致（页脚有"打开数据文件夹"，帮助页有"打开完整的使用说明"）
        environment = dict(os.environ, SEAT_DB_PATH=str(Path(temp) / "shots.db"), SEAT_DATA_DIR=str(Path(temp)),
                           SEAT_DESKTOP="1")
        #桌面版关闭了调试模式，这时runserver要加--insecure才提供脚本和样式文件
        server = subprocess.Popen(
            [sys.executable, "manage.py", "runserver", f"127.0.0.1:{tb.SERVER_PORT}", "--noreload", "--insecure"],
            cwd=PROJECT_DIR, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = subprocess.Popen(
            [tb.EDGE, "--headless=new", f"--remote-debugging-port={tb.DEBUG_PORT}", f"--user-data-dir={Path(temp) / 'edge'}",
             "--no-first-run", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            tb.wait_for_http(tb.SITE, "网页服务")
            targets = json.loads(tb.wait_for_http(f"http://127.0.0.1:{tb.DEBUG_PORT}/json/list", "浏览器"))
            page = tb.CdpPage(next(t for t in targets if t["type"] == "page")["webSocketDebuggerUrl"])
            page.command("Runtime.enable")
            page.command("Page.enable")
            page.command("Emulation.setDeviceMetricsOverride", width=1180, height=760, deviceScaleFactor=1, mobile=False)
            page.command("Page.navigate", url=tb.SITE)
            page.wait("typeof state !== 'undefined' && document.querySelector('.welcome')", "打开网页")
            take_screenshots(page)
            assert page.errors == [], page.errors
            page.sock.close()
        finally:
            tb.close_browser(browser)
            server.terminate()
            server.wait(timeout=20)
            time.sleep(1)
    print(f"已生成 {len(list(SHOTS_DIR.glob('*.png')))} 张截图：{SHOTS_DIR}")
