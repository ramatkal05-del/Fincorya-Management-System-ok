from django.urls import path
from . import views

app_name = "cash"
urlpatterns = [
    path("<int:account_id>/cloture/<int:closure_id>/valider/", views.closure_approve, name="closure_approve"),
    path("", views.cash_list, name="list"),
    path("<int:account_id>/", views.cash_detail, name="detail"),
    path("<int:account_id>/remise/", views.handover_create, name="handover_create"),
    path("remise/<int:handover_id>/confirmer/", views.handover_confirm, name="handover_confirm"),
    path("<int:account_id>/cloture/", views.closure_create, name="closure_create"),
    path("<int:account_id>/cloture/apercu/", views.closure_preview, name="closure_preview"),
]
