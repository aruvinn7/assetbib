from django import template

register = template.Library()

SEP = ' > '


@register.filter
def path_leaf(location):
    """'Angsana > Mess > Mess 1' → 'Mess 1' (nama ruang paling spesifik)."""
    if not location:
        return ''
    return str(location).split(SEP)[-1]


@register.filter
def path_parent(location):
    """'Angsana > Mess > Mess 1' → 'Angsana › Mess' (jalur induknya)."""
    if not location:
        return ''
    return ' › '.join(str(location).split(SEP)[:-1])
