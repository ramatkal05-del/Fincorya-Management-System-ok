from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from PIL import Image, UnidentifiedImageError

from .models import User


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
    new_password1 = forms.CharField(
        label="Nouveau mot de passe", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Utilisez un mot de passe long, unique et difficile à deviner.",
    )
    new_password2 = forms.CharField(
        label="Confirmation du nouveau mot de passe", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
