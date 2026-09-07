from django.urls import path
from . import views

app_name = "reports"
urlpatterns = [path("", views.report_list, name="list"), path("<uuid:public_id>/telecharger/", views.report_download, name="download")]
