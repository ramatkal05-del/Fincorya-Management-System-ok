from django.urls import path
from . import views

app_name = "expenses"
urlpatterns = [
    path("", views.expense_list, name="list"),
    path("nouvelle/", views.expense_create, name="create"),
    path("<int:expense_id>/", views.expense_detail, name="detail"),
    path("<int:expense_id>/decision/", views.expense_decide, name="decide"),
    path("<int:expense_id>/justificatif/", views.receipt_download, name="receipt"),
]
