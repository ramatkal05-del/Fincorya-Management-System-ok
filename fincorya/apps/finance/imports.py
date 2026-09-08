"""Stage external evidence. Staging never creates a ledger entry or real identity."""
from django.core.exceptions import ValidationError
from apps.accounts.permissions import require_finance_access
from apps.audit.services import record
from .locking import ledger_atomic
from .models import ImportRow


def inspect_row(payload):
    issues = list(payload.get("uncertainties", []))
    if not payload.get("effective_from"):
        issues.append("Date d’effet non confirmée")
    if payload.get("kind") == "rule" and not payload.get("stakeholder_id"):
        issues.append("Partie économique à rattacher explicitement")
    if payload.get("kind") == "account":
        if not payload.get("owner_id"):
            issues.append("Titulaire inconnu")
        if payload.get("balance") is None:
            issues.append("Solde inconnu")
        if payload.get("service", "").casefold() in {"m-pesa", "mpesa", "m pesa"}:
            issues.append("Vérifier le rattachement au service Vodacom M-Pesa ; ne pas créer un compte supplémentaire")
    if payload.get("declared_copied_from_theoretical"):
        issues.append("Le solde réel doit provenir d’un comptage indépendant")
    if payload.get("synthetic_adjustment"):
        issues.append("Ajustement artificiel exclu de la comptabilisation")
    if payload.get("kind") == "commission" and not payload.get("stakeholder_id"):
        issues.append("Commission non attribuée")
    if payload.get("name", "").casefold() == "jenovic mpoto":
        payload["canonical_name"] = "Mpoto Jenovic"
    return sorted(set(issues))


@ledger_atomic
def stage_import(*, actor, rows, apply=False):
    require_finance_access(actor, "administer")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValidationError("Le fichier doit contenir une liste d’objets JSON.")
    result = []
    keys = set()
    for row in rows:
        key = row.get("source_key", "")
        if not key or key in keys:
            raise ValidationError("Chaque ligne exige une clé source unique et stable.")
        keys.add(key)
        payload = dict(row)
        anomalies = inspect_row(payload)
        existing = ImportRow.objects.filter(source_key=key).first()
        if existing and existing.payload != payload:
            raise ValidationError(f"La source {key} existe avec un autre contenu ; fournissez une nouvelle version.")
        if apply and not existing:
            existing = ImportRow.objects.create(source_key=key, payload=payload, anomalies=anomalies)
            record(actor=actor, action="FINANCE_STAGE_IMPORT", instance=existing, after={"anomalies": anomalies})
        result.append({"source_key": key, "anomalies": anomalies, "staged": apply})
    return result


@ledger_atomic
def review_import(*, row_id, actor, resolution):
    require_finance_access(actor, "administer")
    row = ImportRow.objects.select_for_update().get(pk=row_id)
    if len(resolution.strip()) < 20:
        raise ValidationError("Documentez la décision et sa pièce justificative (20 caractères minimum).")
    if row.reviewed_by_id:
        raise ValidationError("Cette revue est figée ; préparez une nouvelle version de la source.")
    row.resolution = resolution.strip()
    row.reviewed_by = actor
    row.save(update_fields=["resolution", "reviewed_by"])
    record(actor=actor, action="FINANCE_IMPORT_REVIEW", instance=row,
           after={"resolution": row.resolution, "anomalies": row.anomalies})
    return row
