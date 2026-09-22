"""Rendering helpers shared by every FINCORYA transactional e-mail.

Every notification is described as plain data (title/paragraphs/facts/CTA)
and rendered through the same two templates (`emails/base.html` and
`emails/base.txt`), so the visual identity in branding.py only needs to be
edited in one place.
"""
from django.conf import settings
from django.template.loader import render_to_string

from .branding import BRAND


def render_email(*, subject, title, paragraphs, facts=None, cta_label="", cta_url="", footer_note=""):
    context = {
        "brand": BRAND,
        "site_url": settings.SITE_URL,
        "subject": subject,
        "title": title,
        "paragraphs": paragraphs,
        "facts": facts or [],
        "cta_label": cta_label,
        "cta_url": cta_url,
        "footer_note": footer_note,
    }
    html_body = render_to_string("emails/base.html", context)
    text_body = render_to_string("emails/base.txt", context)
    return html_body, text_body


def absolute_url(path):
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{settings.SITE_URL}{path}"
