from django.utils.functional import SimpleLazyObject

from .roles import get_user_role


def user_roles(request):
    """
    Satu sumber role untuk template: profile Employee (sama dengan roles.py
    yang dipakai decorator), bukan auth Group. Lazy — query hanya jalan
    kalau template benar-benar memakai variabel ini.
    """
    role = SimpleLazyObject(lambda: get_user_role(request.user) or '')
    return {
        'user_role':    role,
        'is_ga':        SimpleLazyObject(lambda: role == 'ga'),
        'is_finance':   SimpleLazyObject(lambda: role == 'finance'),
        'is_user_role': SimpleLazyObject(lambda: role == 'user'),
    }
