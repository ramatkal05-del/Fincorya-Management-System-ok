from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.hashers import check_password
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice
from PIL import Image, UnidentifiedImageError

from .models import RecoveryCode, User


class EmailLoginForm(forms.Form):
    email = forms.EmailField(label="Adresse e-mail", widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))
    password = forms.CharField(label="Mot de passe", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class TokenForm(forms.Form):
    token = forms.CharField(label="Code de sécurité", min_length=6, max_length=12, widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric", "placeholder": "000000"}))


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone", "city", "language", "photo")
        widgets = {
            "first_name": forms.TextInput(attrs={"autocomplete": "given-name"}),
            "last_name": forms.TextInput(attrs={"autocomplete": "family-name"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "phone": forms.TextInput(attrs={"autocomplete": "tel"}),
            "city": forms.TextInput(attrs={"autocomplete": "address-level2"}),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Cette adresse e-mail est déjà utilisée.")
        if self.instance.username == self.instance.email and User.objects.filter(username__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Cette adresse e-mail ne peut pas être utilisée pour ce compte.")
        return email

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        upload = self.files.get("photo")
        if upload is None:
            return photo
        if upload.size > 3 * 1024 * 1024:
            raise forms.ValidationError("La photo ne peut pas dépasser 3 Mo.")
        try:
            image = Image.open(upload)
            image.verify()
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise forms.ValidationError("Formats acceptés : JPEG, PNG ou WebP.")
        except (UnidentifiedImageError, OSError):
            raise forms.ValidationError("Le fichier sélectionné n'est pas une image valide.")
        finally:
            upload.seek(0)
        return photo


class SecurePasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label="Mot de passe actuel", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "autofocus": True}),
    )
    mfa_token = forms.CharField(
        label="Code de sécurité (TOTP ou code de récupération)",
        min_length=6, max_length=12, strip=False,
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric", "placeholder": "000000"}),
        help_text="Requis car l'authentification à deux facteurs reste obligatoire pour modifier le mot de passe.",
    )
    new_password1 = forms.CharField(
        label="Nouveau mot de passe", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Utilisez un mot de passe long, unique et difficile à deviner.",
    )
    new_password2 = forms.CharField(
        label="Confirmation du nouveau mot de passe", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean_mfa_token(self):
        token = (self.cleaned_data.get("mfa_token") or "").replace(" ", "")
        user = self.user
        if not user.totp_enabled:
            # MFA not configured for this account: keep the old-password gate only.
            return token
        if not token:
            raise forms.ValidationError("Le code de sécurité est obligatoire pour modifier le mot de passe.")
        device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        if device and device.verify_token(token):
            return token
        recovery = next((code for code in user.recovery_codes.filter(used_at__isnull=True) if code.matches(token.upper())), None)
        if recovery is not None and user.recovery_codes.filter(pk=recovery.pk, used_at__isnull=True).update(used_at=timezone.now()) == 1:
            return token
        raise forms.ValidationError("Code de sécurité invalide ou expiré.")
