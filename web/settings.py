#Django设置。本项目只用Django处理网页请求：
#数据由seat_random.HistoryManager自己读写SQLite文件，不使用Django的数据库、用户和后台功能
import os
from pathlib import Path

from django.core.management.utils import get_random_secret_key

#项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent

#数据文件夹：存放排座数据库、自动备份和密钥。默认是项目目录下的data文件夹；
#打包成桌面版程序后，程序文件夹不一定可以写入，由启动程序通过环境变量SEAT_DATA_DIR指定位置
DATA_DIR = Path(os.environ.get("SEAT_DATA_DIR", BASE_DIR / "data"))

#排座数据库文件的位置（与main.py使用的是同一个文件）
#可以用环境变量SEAT_DB_PATH指定别的文件，测试时用它换成临时数据库，避免动到正式数据
SEAT_DB_PATH = Path(os.environ.get("SEAT_DB_PATH", DATA_DIR / "seat_history.db"))

#是否作为桌面版程序运行（由desktop/app.py启动时设置）。桌面版多出"打开数据文件夹""打开使用说明"两个功能
DESKTOP_MODE = os.environ.get("SEAT_DESKTOP") == "1"
#桌面版附带的使用说明文件的位置
HELP_FILE = os.environ.get("SEAT_HELP_FILE", "")

#Django用来给防伪令牌签名的密钥。不写在代码里：第一次启动时随机生成，保存在数据文件夹中
SECRET_KEY_FILE = DATA_DIR / "django_secret_key.txt"
if not SECRET_KEY_FILE.exists():
    SECRET_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRET_KEY_FILE.write_text(get_random_secret_key(), encoding="utf-8")
SECRET_KEY = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()

#目前只在本机使用：开启调试模式（由Django直接提供脚本和样式文件），只接受本机的访问
#以后要部署到服务器时，这两项必须修改
#桌面版关闭调试模式（脚本和样式文件由desktop/app.py中的服务提供），出错时不向界面显示程序内部信息
DEBUG = not DESKTOP_MODE
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",   #提供static文件夹中的脚本和样式
    "web",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    #防止别的网站冒充用户向本服务发请求：修改数据的请求必须带上本页面拿到的令牌
    "django.middleware.csrf.CsrfViewMiddleware",
]

ROOT_URLCONF = "web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,           #在web/templates中查找网页
        "OPTIONS": {},
    },
]

STATIC_URL = "static/"

#不使用Django的数据库
DATABASES = {}

#界面文字都写在网页里，时间由seat_random自己记录，不需要Django的多语言和时区功能。
#关闭它们，打包成桌面版时就不必带上Django的语言文件和时区数据
USE_I18N = False
USE_TZ = False
