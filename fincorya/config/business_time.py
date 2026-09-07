from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone


def business_day_bounds(start_date, end_date):
    """Return an inclusive business-date interval as an aware [start, end) range."""
    zone = ZoneInfo(settings.BUSINESS_TIME_ZONE)
    start = datetime.combine(start_date, time.min, tzinfo=zone)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=zone)
    return start, end


def business_date(value=None):
    """Return the calendar date in FINCORYA's configured business timezone."""
    return timezone.localtime(value or timezone.now(), ZoneInfo(settings.BUSINESS_TIME_ZONE)).date()
