from django.urls import path
from . import views

app_name = "operations"
urlpatterns = [
    path("", views.operation_list, name="list"),
    path("nouvelle/", views.operation_create, name="create"),
    path("preview/", views.operation_preview, name="preview"),
    path("<uuid:reference>/", views.operation_detail, name="detail"),
    path("<uuid:reference>/payer/", views.operation_pay, name="pay"),
    path("<uuid:reference>/annuler/", views.operation_cancel, name="cancel"),
    path("<uuid:reference>/corriger/", views.operation_revise, name="revise"),
]
