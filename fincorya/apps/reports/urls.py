from django.urls import path
from . import views

app_name = "reports"
urlpatterns = [
    path("situation-septembre-2026/", views.month_end_statement, name="month_end_statement"),
    path("centre/", views.report_center, name="center"),
    path("mensuel/", views.monthly_report_create, name="monthly"),
    path("<uuid:public_id>/telecharger/", views.report_download, name="download"),
]
