#Django设置。本项目只用Django处理网页请求：
#数据由seat_random.HistoryManager自己读写SQLite文件，不使用Django的数据库、用户和后台功能
import os
from pathlib import Path

from django.core.management.utils import get_random_secret_key

#项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent

#排座数据库文件的位置（与main.py使用的是同一个文件）
#可以用环境变量SEAT_DB_PATH指定别的文件，测试时用它换成临时数据库，避免动到正式数据
SEAT_DB_PATH = Path(os.environ.get("SEAT_DB_PATH", BASE_DIR / "data" / "seat_history.db"))

#Django用来给防伪令牌签名的密钥。不写在代码里：第一次启动时随机生成，保存在data文件夹中
SECRET_KEY_FILE = BASE_DIR / "data" / "django_secret_key.txt"
if not SECRET_KEY_FILE.exists():
    SECRET_KEY_FILE.parent.mkdir(exist_ok=True)
    SECRET_KEY_FILE.write_text(get_random_secret_key(), encoding="utf-8")
SECRET_KEY = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()

#目前只在本机使用：开启调试模式（由Django直接提供脚本和样式文件），只接受本机的访问
#以后要部署到服务器时，这两项必须修改
DEBUG = True
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

USE_TZ = True
