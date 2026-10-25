from django import template

register = template.Library()

@register.filter
def abs_value(value):
    return abs(value)
