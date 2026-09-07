from django.urls import path
from . import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("verify/", views.verify_view, name="verify"),
    path("verify/email/send/", views.send_email_otp, name="send_email_otp"),
    path("totp/setup/", views.totp_setup, name="totp_setup"),
    path("recovery-codes/", views.recovery_codes, name="recovery_codes"),
    path("logout/", views.logout_view, name="logout"),
    path("profil/", views.profile_edit, name="profile"),
    path("profil/mot-de-passe/", views.password_change, name="password_change"),
    path("users/<int:user_id>/photo/", views.profile_photo, name="profile_photo"),
]
