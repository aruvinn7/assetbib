def is_ga(user):
    return hasattr(user.employee, "ga_profile")


def is_finance(user):
    return hasattr(user.employee, "finance_profile")

def is_user_role(user):
    """Cek apakah user adalah karyawan biasa (UserProfile)"""
    return (
        user.is_authenticated
        and hasattr(user, 'employee')
        and hasattr(user.employee, 'user_profile')
    )


def get_user_role(user):
    """Return role string dari user yang sedang login"""
    if not user.is_authenticated or not hasattr(user, 'employee'):
        return None
    emp = user.employee
    if hasattr(emp, 'ga_profile'):
        return 'ga'
    if hasattr(emp, 'finance_profile'):
        return 'finance'
    if hasattr(emp, 'user_profile'):
        return 'user'
    return None