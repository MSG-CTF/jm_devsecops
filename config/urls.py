from django.contrib import admin
from django.http import JsonResponse
from django.urls import path


def index(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", index),
]
