from django import template


register = template.Library()


@register.filter
def accessible_widget(field):
    """Render a bound field with stable links to its help and error messages."""
    identifiers = []
    attrs = {}
    if field.help_text:
        identifiers.append(f"{field.auto_id}_help")
    if field.errors:
        attrs["aria-invalid"] = "true"
        identifiers.append(f"{field.auto_id}_errors")
    if identifiers:
        attrs["aria-describedby"] = " ".join(identifiers)
    return field.as_widget(attrs=attrs)
