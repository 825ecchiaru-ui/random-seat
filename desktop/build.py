#打包脚本：生成便携版和安装包
#用法（用打包环境randomseat-build的Python运行，见desktop/README.md）：
#    python desktop/build.py
#输出在desktop/dist：
#    座位随机重排-便携版-版本号.zip      解压后双击其中的exe即可使用
#    座位随机重排-安装包-版本号.exe      安装程序
#    使用说明.html、使用说明.pdf         使用说明（两个包里也各带了一份）
#
#打包的方式（为什么不用PyInstaller）：
#  PyInstaller把Python和程序合成一个exe，这样的exe没有数字签名、每次打包都是一个"从没见过"的新文件，
#  很容易被Windows安全中心等杀毒软件的机器学习模型误判为木马（实际遇到过：Trojan:Win32/Bearfoos.B!ml）。
#  这里改为：
#    - 程序包里直接放Python官方发布的"嵌入式"运行环境。其中的exe和dll都带有Python软件基金会的数字签名
#    - 用到的工具包（numpy、Django、pywebview）原样放进去，它们是被大量使用的公开文件
#    - 我们自己的代码以普通的.py脚本文件放进去
#    - 只有一个自己编译的、很小的启动器exe（launcher/launcher.c），负责用自带的Python运行程序
#  这样程序包里就没有"把一堆数据塞在exe后面"这种容易被误判的文件了。
#
#步骤：
#  1. 生成图标
#  2. 准备程序文件夹：Python运行环境、工具包、程序代码
#  3. 编译启动器
#  4. 生成使用说明（把截图嵌进HTML，并用Edge另存为PDF）
#  5. 整理成便携版并压缩成zip；用NSIS生成安装包
import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = DESKTOP_DIR.parent
BUILD_DIR = DESKTOP_DIR / "build"       #打包过程中的临时文件
DIST_DIR = DESKTOP_DIR / "dist"         #最终产物
CACHE_DIR = DESKTOP_DIR / "cache"       #下载下来的Python运行环境，留着下次打包时用
sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(DESKTOP_DIR))

APP_NAME = "座位随机重排"

#Python官方的嵌入式运行环境。版本要与打包环境的Python一致（都是3.10），工具包才能通用。
#SHA256是这个文件的"指纹"：下载后核对，确保拿到的是原样的官方文件
PYTHON_URL = "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip"
PYTHON_SHA256 = "608619f8619075629c9c69f361352a0da6ed7e62f83a0e19c63e0ea32eb7629d"

#程序运行需要的工具包（它们依赖的其他包会被一并装上）
PACKAGES = ["numpy==2.2.*", "django==5.2.8", "pywebview==6.2.1"]

#本机工具的位置
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
MAKENSIS = Path(r"C:\Program Files (x86)\NSIS\makensis.exe")
VCVARS = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat")


def step(text):
    print(f"\n=== {text} ===", flush=True)


def folder_size(folder):
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())


def prepare_runtime(internal_dir):
    #把Python运行环境、工具包和程序代码放进程序文件夹的_internal里
    internal_dir.mkdir(parents=True)

    # --- Python运行环境 ---
    CACHE_DIR.mkdir(exist_ok=True)
    archive = CACHE_DIR / PYTHON_URL.rsplit("/", 1)[1]
    if not archive.exists():
        print(f"下载 {PYTHON_URL}")
        urllib.request.urlretrieve(PYTHON_URL, archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert digest == PYTHON_SHA256, f"Python运行环境的文件与预期不符（{digest}），请删除 {archive} 后重试"
    with zipfile.ZipFile(archive) as z:
        z.extractall(internal_dir)

    #python310._pth决定Python到哪些位置找代码（每行一个，相对于这个文件所在的文件夹）。
    #有这个文件时，Python只看这里列出的位置，不受电脑上其他Python或环境变量的影响
    (internal_dir / "python310._pth").write_text(
        "python310.zip\n"       #Python自带的标准库
        ".\n"
        "site-packages\n"       #工具包
        "app\n",                #程序代码
        encoding="utf-8")

    # --- 工具包 ---
    packages_dir = internal_dir / "site-packages"
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "--no-compile", "--target", str(packages_dir), *PACKAGES], check=True)
    #去掉用不到的大块头，减小体积：pip附带的命令行小工具、测试代码、Django的后台管理和地理信息功能，
    #以及Django除简体中文和英文之外的语言文件（项目已关闭Django的多语言功能）
    remove = [packages_dir / "bin", packages_dir / "django" / "contrib" / "admin",
              packages_dir / "django" / "contrib" / "admindocs", packages_dir / "django" / "contrib" / "gis"]
    remove += [d for d in (packages_dir / "numpy").rglob("tests") if d.is_dir()]
    for locale_dir in (packages_dir / "django").rglob("locale"):
        remove += [d for d in locale_dir.iterdir() if d.is_dir() and d.name not in ("en", "zh_Hans")]
    for folder in remove:
        if folder.exists():
            shutil.rmtree(folder)

    # --- 程序代码 ---
    app_dir = internal_dir / "app"
    skip_cache = shutil.ignore_patterns("__pycache__")
    shutil.copytree(PROJECT_DIR / "seat_random", app_dir / "seat_random", ignore=skip_cache)
    shutil.copytree(PROJECT_DIR / "web", app_dir / "web", ignore=skip_cache)      #包括网页、脚本、样式和图标
    shutil.copy(DESKTOP_DIR / "app.py", app_dir / "app.py")
    shutil.copy(DESKTOP_DIR / "assets" / "app.ico", app_dir / "app.ico")

    #预先把.py编译成.pyc，使用者第一次打开程序时会快一些。用程序自带的Python来编译，版本才对得上
    subprocess.run([str(internal_dir / "python.exe"), "-m", "compileall", "-q", str(packages_dir), str(app_dir)],
                   check=True)


def build_launcher(version, target):
    #编译启动器（launcher/launcher.c），带上图标、版本信息和程序清单
    work = BUILD_DIR / "launcher"
    work.mkdir()
    numbers = ",".join((version.split(".") + ["0"] * 4)[:4])
    icon = str(DESKTOP_DIR / "assets" / "app.ico").replace("\\", "\\\\")
    manifest = str(DESKTOP_DIR / "launcher" / "launcher.manifest").replace("\\", "\\\\")
    #资源文件：图标、程序清单、在exe的"属性"里显示的版本信息。第一行声明本文件是UTF-8编码
    (work / "launcher.rc").write_text(f'''#pragma code_page(65001)
1 ICON "{icon}"
1 24 "{manifest}"
1 VERSIONINFO
FILEVERSION {numbers}
PRODUCTVERSION {numbers}
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "080404b0"
    BEGIN
      VALUE "ProductName", "{APP_NAME}"
      VALUE "FileDescription", "{APP_NAME}"
      VALUE "FileVersion", "{version}"
      VALUE "ProductVersion", "{version}"
      VALUE "LegalCopyright", "MIT License"
      VALUE "OriginalFilename", "{APP_NAME}.exe"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x0804, 1200
  END
END
''', encoding="utf-8")
    source = DESKTOP_DIR / "launcher" / "launcher.c"
    #vcvars64.bat设置好编译器需要的环境；rc编译资源文件；cl编译并链接成不显示终端窗口的程序
    #/MT：把C语言的运行库直接放进exe里，使用者的电脑上不需要另外安装
    command = (f'"{VCVARS}" >nul && rc /nologo /fo launcher.res launcher.rc && '
               f'cl /nologo /O2 /MT /W3 /utf-8 /DUNICODE /D_UNICODE "{source}" launcher.res '
               f'/Fe:launcher.exe /link /SUBSYSTEM:WINDOWS user32.lib kernel32.lib')
    result = subprocess.run(command, shell=True, cwd=work, capture_output=True, text=True, errors="replace")
    assert result.returncode == 0 and (work / "launcher.exe").exists(), result.stdout + result.stderr
    shutil.copy(work / "launcher.exe", target)


def build_guide(version, target_html, target_pdf):
    #把使用说明原稿里引用的图片直接嵌进HTML文件（这样单独一个文件就能发给别人），再用Edge另存成PDF
    guide_dir = DESKTOP_DIR / "guide"
    html = (guide_dir / "guide.html").read_text(encoding="utf-8").replace("{{VERSION}}", version)

    def embed(match):
        image = (guide_dir / match.group(1)).resolve()
        data = base64.b64encode(image.read_bytes()).decode("ascii")
        return f'src="data:image/png;base64,{data}"'

    html, count = re.subn(r'src="([^"]+\.png)"', embed, html)
    assert count >= 10, "使用说明里的图片比预期的少，请先运行 desktop/make_screenshots.py"
    target_html.write_text(html, encoding="utf-8")

    #--user-data-dir：让这次运行使用一份独立的浏览器设置。否则，如果电脑上正开着Edge，
    #这条命令会把任务转交给已经打开的Edge然后立刻结束，PDF就生成不出来
    result = subprocess.run([str(EDGE), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                             f"--user-data-dir={BUILD_DIR / 'edge-profile'}", "--no-first-run",
                             f"--print-to-pdf={target_pdf}", target_html.as_uri()],
                            capture_output=True, timeout=180)
    #电脑上正开着Edge时，这条命令有时会立刻返回，PDF由后台的进程稍后写出。所以这里等文件出现并且大小不再变化
    size = -1
    for _ in range(120):
        time.sleep(0.5)
        if target_pdf.exists():
            if target_pdf.stat().st_size == size and size > 100_000:
                break
            size = target_pdf.stat().st_size
    else:
        raise AssertionError("使用说明的PDF没有生成：" + result.stderr.decode("utf-8", "replace"))


def main():
    import seat_random
    version = seat_random.__version__
    print(f"打包 {APP_NAME} {version}")

    #清空上一次的临时文件和产物（都是本脚本自己生成的，可以随时重新生成）
    for folder in (BUILD_DIR, DIST_DIR):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir()

    step("1/5 生成图标")
    import make_icon
    (DESKTOP_DIR / "assets").mkdir(exist_ok=True)
    (DESKTOP_DIR / "assets" / "app.ico").write_bytes(make_icon.build_icon([16, 24, 32, 48, 64, 256]))
    (PROJECT_DIR / "web" / "static" / "web" / "favicon.ico").write_bytes(make_icon.build_icon([16, 32, 48, 64]))
    (DESKTOP_DIR / "assets" / "app.png").write_bytes(make_icon.png_bytes(128, make_icon.render(128)))

    step("2/5 准备程序文件夹（Python运行环境、工具包、程序代码）")
    #安装包和便携版的程序文件相同，区别只是便携版多一个portable.txt标记文件（数据放在程序旁边）
    files_dir = BUILD_DIR / "files"
    prepare_runtime(files_dir / "_internal")

    step("3/5 编译启动器")
    build_launcher(version, files_dir / f"{APP_NAME}.exe")

    step("4/5 生成使用说明")
    guide_html = DIST_DIR / "使用说明.html"
    guide_pdf = DIST_DIR / "使用说明.pdf"
    build_guide(version, guide_html, guide_pdf)
    shutil.copy(guide_html, files_dir / "使用说明.html")
    shutil.copy(guide_pdf, files_dir / "使用说明.pdf")

    step("5/5 生成便携版和安装包")
    portable_name = f"{APP_NAME}-便携版-{version}"
    portable_dir = BUILD_DIR / portable_name
    shutil.copytree(files_dir, portable_dir)
    (portable_dir / "portable.txt").write_text(
        "这是便携版的标记文件，请不要删除。\n"
        "有这个文件时，程序把数据（班级、名单、排座记录）保存在旁边的 data 文件夹里，\n"
        "整个文件夹可以复制到U盘或别的电脑上继续使用。\n", encoding="utf-8")
    portable_zip = DIST_DIR / f"{portable_name}.zip"
    with zipfile.ZipFile(portable_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file in sorted(portable_dir.rglob("*")):
            if file.is_file():
                archive.write(file, Path(portable_name) / file.relative_to(portable_dir))

    installer = DIST_DIR / f"{APP_NAME}-安装包-{version}.exe"
    subprocess.run([str(MAKENSIS), "/V2", "/INPUTCHARSET", "UTF8",
                    f"/DVERSION={version}", f"/DSOURCE={files_dir}", f"/DOUTFILE={installer}",
                    f"/DICON={DESKTOP_DIR / 'assets' / 'app.ico'}",
                    f"/DSIZE_KB={folder_size(files_dir) // 1024}",
                    str(DESKTOP_DIR / "installer.nsi")], check=True)

    step("完成")
    print(f"程序文件夹（未压缩）：{folder_size(files_dir) / 1e6:.0f} MB")
    for file in sorted(DIST_DIR.iterdir()):
        print(f"  {file.name}    {file.stat().st_size / 1e6:.1f} MB")
    print(f"便携版文件夹（测试用）：{portable_dir}")


if __name__ == "__main__":
    main()
