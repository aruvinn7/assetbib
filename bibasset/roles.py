def is_ga(user):
    return hasattr(user.employee, "ga_profile")


def is_finance(user):
    return hasattr(user.employee, "finance_profile")

def can_report_damage(user):
    """Siapa pun yang punya akses (GA, Finance, Karyawan) boleh melaporkan kerusakan aset."""
    return is_ga(user) or is_finance(user) or is_user_role(user)


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


# Halaman pertama tiap peran. Ringkasan (dashboard) khusus GA.
HOME_URL_NAMES = {'ga': 'dashboard', 'finance': 'value_dashboard', 'user': 'user_dashboard'}


def home_url_name(user):
    """Nama URL beranda untuk peran user, atau None bila akunnya belum punya akses."""
    return HOME_URL_NAMES.get(get_user_role(user))