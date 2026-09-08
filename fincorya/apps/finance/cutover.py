import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.permissions import require_finance_access
from apps.audit.services import record
from apps.cash.models import CashAccount, GlobalCashAccount
from .locking import ledger_atomic
from .models import (AccountNature, AccountReconciliation, AccountType, FinancialAccount,
                     ImportRow, LegacyMigrationRun, MigrationStatus)


@ledger_atomic
def migrate_opening_balances(*, actor, cutover_at, apply=False, expected_hash=""):
    from .services import ledger_balance, prepare_batch, post_batch
    require_finance_access(actor, "administer")
    if timezone.is_naive(cutover_at) or cutover_at > timezone.now():
        raise ValidationError("La bascule doit être une date passée ou présente avec fuseau explicite.")
    previous = LegacyMigrationRun.objects.filter(status=MigrationStatus.APPLIED).first()
    if apply and previous:
        if previous.cutover_at == cutover_at and previous.preview_hash == expected_hash:
            return previous
        raise ValidationError("Une reprise existe déjà. Une seconde reprise doublerait les soldes.")
    rows = []
    # Same lock order as domain cash services (the outer ledger lock serializes
    # participating writes); read historical balances as of cutover, not today.
    legacy_accounts = [*CashAccount.objects.select_for_update().order_by("pk"),
                       *GlobalCashAccount.objects.select_for_update().order_by("pk")]
    for legacy in legacy_accounts:
        is_global = isinstance(legacy, GlobalCashAccount)
        movements = legacy.movements.order_by("created_at", "pk")
        at_cutover = movements.filter(created_at__lte=cutover_at).last()
        latest = movements.last()
        anomalies = []
        balance = at_cutover.balance_after if at_cutover else 0
        if legacy.balance != (latest.balance_after if latest else 0):
            anomalies.append("Solde legacy différent de sa dernière écriture")
        if movements.filter(created_at__gt=cutover_at).exists():
            anomalies.append("Mouvements après la bascule : choisir un arrêt contrôlé plus récent")
        if balance < 0:
            anomalies.append("Solde négatif à qualifier")
        rows.append({"legacy_id": legacy.pk, "source_model": legacy._meta.label,
                     "currency": legacy.currency.code, "legacy_balance": str(balance),
                     "anomalies": anomalies, "global": is_global})
    unresolved = ImportRow.objects.exclude(anomalies=[]).filter(resolution="").count()
    report = {"strategy": "OPENING_ONLY", "cutover_at": cutover_at.isoformat(), "accounts": rows,
              "unresolved_import_rows": unresolved}
    digest = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
    if not apply:
        return {**report, "preview_hash": digest, "applied": False}
    if not settings.FINANCE_LEDGER_ENABLED:
        raise ValidationError("Le moteur est désactivé. La reprise exige une bascule explicitement configurée.")
    if expected_hash != digest:
        raise ValidationError("Simulation absente ou périmée : fournissez son empreinte exacte.")
    if unresolved or any(row["anomalies"] for row in rows):
        raise ValidationError("Les anomalies de reprise doivent être résolues avant la bascule.")
    if FinancialAccount.objects.filter(cutover_at__isnull=False).exists():
        raise ValidationError("Des comptes ont déjà une origine de reprise ; réconciliation manuelle requise.")
    run = LegacyMigrationRun.objects.create(cutover_at=cutover_at, strategy="OPENING_ONLY", requested_by=actor,
                                            preview_hash=digest, report=report)
    for legacy, row in zip(legacy_accounts, rows):
        relation = "legacy_global_account" if row["global"] else "legacy_cash_account"
        account = FinancialAccount.objects.create(
            **{relation: legacy}, code=f"LEGACY-{'GLOBAL' if row['global'] else 'CASH'}-{legacy.pk}",
            name=str(legacy), account_type=AccountType.GLOBAL_CASH if row["global"] else AccountType.AGENT_CASH,
            nature=AccountNature.ASSET, currency=legacy.currency, cutover_at=cutover_at,
            responsible_user=legacy.administrator if row["global"] else legacy.agent,
        )
        from decimal import Decimal
        balance = Decimal(row["legacy_balance"])
        if balance:
            counterpart, _ = FinancialAccount.objects.get_or_create(
                code=f"OPENING-EQUITY-{legacy.currency.code}",
                defaults={"name": f"Solde d’ouverture {legacy.currency.code}", "account_type": AccountType.CAPITAL,
                          "nature": AccountNature.EQUITY, "currency": legacy.currency})
            batch = prepare_batch(actor=actor, idempotency_key=f"opening-{row['source_model']}-{legacy.pk}",
                event_type="LEGACY_OPENING", effective_at=cutover_at, description=f"Reprise {account.code}",
                source_model=row["source_model"], source_id=legacy.pk, lines=[
                    {"account": account, "currency": legacy.currency, "side": "DEBIT", "amount": balance},
                    {"account": counterpart, "currency": legacy.currency, "side": "CREDIT", "amount": balance}])
            post_batch(batch_id=batch.pk, actor=actor)
        migrated = ledger_balance(account)
        AccountReconciliation.objects.create(run=run, account=account, currency=legacy.currency,
            legacy_balance=balance, migrated_balance=migrated, difference=balance - migrated,
            difference_origin="" if balance == migrated else "Écart de reprise")
    if run.reconciliations.exclude(difference=0).exists():
        raise ValidationError("Réconciliation incomplète ; reprise annulée.")
    run.status, run.applied_at = MigrationStatus.APPLIED, timezone.now()
    run.approved_at, run.approved_by = timezone.now(), actor
    run.save()
    record(actor=actor, action="FINANCE_CUTOVER", instance=run, after=report)
    return run


def active_cutover():
    if not settings.FINANCE_LEDGER_ENABLED:
        return None
    run = LegacyMigrationRun.objects.filter(status=MigrationStatus.APPLIED, approved_at__isnull=False).first()
    if run is None:
        raise ValidationError("Le moteur est configuré mais aucune bascule réconciliée n’est approuvée.")
    return run
