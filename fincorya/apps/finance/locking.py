"""One transaction lock orders posting, draft edits, cutovers and period locking.

This intentionally favors correctness over posting throughput. PostgreSQL is
required to exercise the lock; SQLite remains a development fallback.
"""
from contextvars import ContextVar
from functools import wraps

from django.db import transaction

writing = ContextVar("finance_service_write", default=False)
monthly_close = ContextVar("finance_monthly_close", default=None)


def ledger_atomic(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        from .models import LedgerMutex
        with transaction.atomic():
            LedgerMutex.objects.get_or_create(pk=1)
            LedgerMutex.objects.select_for_update().get(pk=1)
            return function(*args, **kwargs)
    return wrapped


def save_internal(instance, **kwargs):
    token = writing.set(True)
    try:
        instance.save(**kwargs)
    finally:
        writing.reset(token)
