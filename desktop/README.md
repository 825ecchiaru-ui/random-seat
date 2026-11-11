# 桌面版（Windows 一键运行程序）

这个文件夹负责把项目打包成普通人可以直接双击使用的 Windows 程序，提供两种形式：

| 形式 | 文件 | 用法 |
|---|---|---|
| 便携版 | `dist/座位随机重排-便携版-版本号.zip` | 解压后双击里面的 `座位随机重排.exe`，数据保存在程序旁边的 `data` 文件夹 |
| 安装包 | `dist/座位随机重排-安装包-版本号.exe` | 双击安装，不需要管理员权限；数据保存在个人文件夹的 `AppData\Roaming\RandomSeat` |

两种形式都附带 `使用说明.html` 和 `使用说明.pdf`（给最终使用者看的说明，包含算法的通俗解释）。

## 它是怎么工作的

桌面版没有另写一套界面。启动器用程序自带的 Python 运行 [app.py](app.py)，它在本机启动网页服务（只有本机能访问），
然后打开一个程序窗口来显示 `web/` 里的网页。窗口用 Windows 自带的 WebView2 组件显示，
没有地址栏和标签页，使用者不会看到网址，也不需要操作终端。所以桌面版和网页版的功能完全一致，改界面只需要改一处。

- Windows 11 自带 WebView2；Windows 10 一般已通过系统更新装上。万一没有，程序自动改用默认浏览器显示界面，并显示一个提示框，点提示框的"确定"即退出。
- 再次双击已经在运行的程序，不会启动第二份，只会把已有的窗口调到前面。
- 启动失败时弹出消息框，并把完整的错误记入数据文件夹的 `logs\app.log`。

## 程序包的构成，以及为什么不用 PyInstaller

```
座位随机重排.exe        启动器：很小的一个程序，只负责用自带的 Python 运行下面的代码
使用说明.html / .pdf
portable.txt            只有便携版有：表示数据放在程序旁边
_internal/
├── pythonw.exe 等      Python 官方发布的"嵌入式"运行环境，带 Python 软件基金会的数字签名
├── site-packages/      用到的工具包：numpy、Django、pywebview 及其依赖，原样放入
└── app/                本项目的代码（seat_random、web、app.py），普通的 .py 文件
```

最初是用 PyInstaller 打包的，它把 Python 和程序合成一个 exe。结果这个 exe 被 Windows 安全中心判为
`Trojan:Win32/Bearfoos.B!ml` 并自动隔离。名字里的 `!ml` 表示这是机器学习模型按"长得像"做出的推断，
不是匹配到了已知病毒；未签名的 PyInstaller 程序经常被这样误判。试验表明判定只跟那一个具体文件有关：
用同样的选项重新打包一份，本机扫描就是无威胁。也就是说它是概率性的，每次打包都可能中招，没有可以删掉的"坏特征"。

所以改成了现在的方式：程序包里的可执行文件要么带数字签名（Python、微软的组件），要么是被大量使用的公开文件（工具包），
只有启动器是自己编译的，而它很小、不携带任何数据。按这种方式打包后，把便携版解压到桌面实际运行并观察了六分钟，
安全中心没有报毒；用安全中心的命令行扫描启动器、便携版压缩包和安装包，也都没有发现威胁。

这不是绝对的保证：启动器和安装包仍然没有数字签名，将来仍有可能被某个杀毒软件误判。彻底的办法是购买代码签名证书。
如果再遇到误报，可以在 <https://www.microsoft.com/wdsi/filesubmission> 向微软提交文件申请复核（选"软件开发者"）。

本机复查的方法（只检测，不隔离）：

```
"%ProgramFiles%\Windows Defender\MpCmdRun.exe" -Scan -ScanType 3 -File 要检查的文件 -DisableRemediation
```

文件名含中文时这条命令的输出可能不完整，可以先把文件复制成英文名再扫描。

## 文件说明

| 文件 | 作用 |
|---|---|
| [app.py](app.py) | 启动程序：确定数据文件夹、启动服务、打开窗口、关窗后退出 |
| [build.py](build.py) | 打包脚本：一条命令生成便携版、安装包和使用说明 |
| [launcher/](launcher/) | 启动器的源代码（C 语言）和程序清单，由 `build.py` 调用编译器编译 |
| [installer.nsi](installer.nsi) | 安装包脚本（NSIS），由 `build.py` 调用 |
| [make_icon.py](make_icon.py) | 生成程序图标 |
| [make_screenshots.py](make_screenshots.py) | 自动操作界面，生成使用说明里的截图 |
| [guide/guide.html](guide/guide.html) | 使用说明的原稿；截图在 `guide/shots` |
| [test_app.py](test_app.py) | 启动程序的测试，可以测直接运行的 `app.py`，也可以测打包好的 exe |
| [test_release.py](test_release.py) | 发布包的测试：解压便携版、安装、运行、卸载 |
| `assets/` | 图标文件 |
| `build/`、`dist/`、`cache/` | 打包的临时文件、最终产物、下载的 Python 运行环境，都不纳入 git |

## 怎样打包

需要这台电脑上装有以下工具（位置写在 `build.py` 开头，装在别处时改那里）：

- Edge：用来把使用说明另存为 PDF。
- [NSIS](https://nsis.sourceforge.io/)：用来制作安装包。
- Visual Studio 的 C 编译器（安装"生成工具"即可）：用来编译启动器。

第一次打包需要联网，下载 Python 官方的运行环境（约 8 MB，之后保存在 `cache/` 里）；每次打包都会联网下载工具包。

**第一次：建立打包环境**（与平时开发用的 `randomseatenv` 分开。它用来运行打包脚本和桌面版的测试；程序包里的工具包是打包时另外下载的，不取自这个环境）

```
conda create -n randomseat-build python=3.10 pip
conda activate randomseat-build
pip install "numpy==2.2.*" "django==5.2.8" pywebview
```

**每次打包**

```
conda activate randomseat-build
python desktop/build.py
python desktop/test_release.py
```

第二条命令生成发布包，大约两分钟；第三条命令对发布包做实际的解压、安装、运行和卸载测试。

## 发布新版本时

1. 修改 `seat_random/__init__.py` 里的 `__version__`。界面、安装包和使用说明都从这里读取版本号。
2. 界面有变化时，用 `randomseatenv` 环境运行 `python desktop/make_screenshots.py` 更新使用说明里的截图，
   并检查 [guide/guide.html](guide/guide.html) 的文字是否需要修改。
3. 按上面的"每次打包"操作。

使用者升级时：便携版把旧文件夹里的 `data` 文件夹复制到新文件夹；安装版直接运行新的安装包覆盖安装，数据不受影响。

## 测试

| 命令 | 用哪个环境 | 作用 |
|---|---|---|
| `python desktop/test_app.py` | `randomseat-build` | 直接运行 `app.py`：打开窗口、走完一次排座、重复启动、关窗退出、再次打开数据仍在 |
| `python desktop/test_app.py 某个路径\座位随机重排.exe` | 任意 | 同上，但测试的是打包好的程序 |
| `python desktop/test_release.py` | `randomseat-build` | 便携版 zip、没有 WebView2 时的备用方式、安装包的安装与卸载 |

测试时程序窗口被放在屏幕之外，不会打扰正在使用电脑的人；数据使用临时文件夹。
安装包测试会在桌面和开始菜单短暂出现快捷方式，测试结束时由卸载程序删除；如果这台电脑上已经正式安装了本程序，这一项会自动跳过。

## 已知的限制

- **启动器和安装包没有数字签名**。从网上下载后第一次运行时，Windows 可能弹出"Windows 已保护你的电脑"，需要点"更多信息"→"仍要运行"（使用说明里写了）。要消除这个提示需要购买代码签名证书。
- **只支持 64 位的 Windows 10 和 11**。
- 窗口组件的缓存和界面的显示偏好保存在 `AppData\Local\RandomSeat\window`，便携版也是如此（不放在程序文件夹里，免得越用越大）。它不包含任何班级数据。
