#Django的命令入口。启动网页服务：python manage.py runserver
#启动后用浏览器打开 http://127.0.0.1:8000/
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "web.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
