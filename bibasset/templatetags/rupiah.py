from django import template

register = template.Library()


@register.filter
def rupiah(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return value
    return "Rp {:,.0f}".format(value).replace(",", ".")
