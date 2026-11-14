# views/utils.py
# Shared role helpers dan imports umum

from ..roles import is_ga, is_finance


def is_ga_or_finance(user):
    return is_ga(user) or is_finance(user)