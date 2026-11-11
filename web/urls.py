#网址与处理函数的对应关系。网址中的班级名称和学生编号由浏览器编码后传入
from django.urls import path

from . import views

urlpatterns = [
    path("", views.index),

    path("api/classes", views.classes),
    path("api/classes/<str:class_name>", views.class_detail),
    path("api/classes/<str:class_name>/seats", views.seats),
    path("api/classes/<str:class_name>/students", views.students),
    path("api/classes/<str:class_name>/students/<str:student_id>", views.student_detail),
    path("api/classes/<str:class_name>/students/<str:student_id>/history", views.student_history),
    path("api/classes/<str:class_name>/preview", views.preview),
    path("api/classes/<str:class_name>/score", views.score),
    path("api/classes/<str:class_name>/assignments", views.assignments),
    path("api/classes/<str:class_name>/assignments/latest", views.latest_assignment),
    path("api/integrity", views.integrity),
    path("api/app", views.app_info),
    path("api/app/open", views.app_open),
]
