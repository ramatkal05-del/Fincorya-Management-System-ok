from django import forms
from PIL import Image, UnidentifiedImageError

from .models import ApprovalDecision, Expense
from apps.accounts.models import Role, User


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ("category", "agent", "label", "amount", "currency", "incurred_on", "receipt")
        widgets = {"incurred_on": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["agent"].queryset = User.objects.filter(role=Role.AGENT, is_active=True).order_by("first_name", "email")

    def clean_receipt(self):
        receipt = self.cleaned_data.get("receipt")
        if not receipt:
            return receipt
        if receipt.size > 5 * 1024 * 1024:
            raise forms.ValidationError("Le justificatif ne peut pas dépasser 5 Mo.")
        extension = receipt.name.rsplit(".", 1)[-1].lower() if "." in receipt.name else ""
        if extension not in {"pdf", "png", "jpg", "jpeg"}:
            raise forms.ValidationError("Formats acceptés : PDF, PNG et JPEG.")
        try:
            if extension == "pdf":
                if receipt.read(5) != b"%PDF-":
                    raise forms.ValidationError("Le justificatif PDF est invalide.")
            else:
                image = Image.open(receipt)
                image.verify()
                expected = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG"}[extension]
                if image.format != expected:
                    raise forms.ValidationError("Le contenu du justificatif ne correspond pas à son extension.")
        except (UnidentifiedImageError, OSError):
            raise forms.ValidationError("Le justificatif image est invalide.")
        finally:
            receipt.seek(0)
        return receipt


class ExpenseDecisionForm(forms.Form):
    decision = forms.ChoiceField(label="Décision", choices=ApprovalDecision.choices)
    comment = forms.CharField(label="Commentaire", required=False, widget=forms.Textarea(attrs={"rows": 3}))
