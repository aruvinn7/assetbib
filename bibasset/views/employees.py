# views/employees.py
# Data karyawan & akun login. Khusus GA: hanya GA yang bisa melihat, menambah, mengubah,
# menonaktifkan, menghapus, dan mengatur ulang password karyawan.
# Password tersimpan sebagai hash (tidak bisa dibaca siapa pun); GA cukup mengatur password baru,
# yang ditampilkan sekali saja setelah disimpan.

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..decorators import role_required
from ..forms import (
    ACCESS_CHOICES, EmployeeForm, check_password_rules, employee_access, flash_form_errors, generate_password,
)
from ..models import Department, Employee
from ..roles import is_ga

ACCESS_FILTERS = {
    'ga':       Q(ga_profile__isnull=False),
    'finance':  Q(finance_profile__isnull=False),
    'user':     Q(user_profile__isnull=False),
    'inactive': Q(user__is_active=False),
}


def _form_context(request, emp=None):
    posted = request.POST if request.method == 'POST' else None
    return {
        'emp_obj':        emp,
        'departments':    Department.objects.order_by('name'),
        'access_choices': ACCESS_CHOICES,
        'posted':         posted,
        'checked_access': posted.getlist('access') if posted else (employee_access(emp) if emp else ['user']),
    }


def _hand_over(request, emp, password):
    """Simpan password baru di sesi (server) agar tampil sekali di halaman karyawan, lalu dibuang.
    Sengaja tidak lewat messages karena messages bisa tersimpan di cookie."""
    request.session['new_credential'] = {'pk': emp.pk, 'password': password}


@role_required(is_ga)
def employee_list(request):
    search = request.GET.get('q', '').strip()
    access = request.GET.get('akses', '')

    base = Employee.objects.select_related('user', 'department', 'ga_profile', 'finance_profile', 'user_profile')
    employees = base.annotate(
        held=Count('held_assets', filter=Q(held_assets__status='active')),
    ).order_by('nama')
    if access in ACCESS_FILTERS:
        employees = employees.filter(ACCESS_FILTERS[access])
    if search:
        employees = employees.filter(
            Q(nama__icontains=search) | Q(employee_id__icontains=search) | Q(user__username__icontains=search) |
            Q(jabatan__icontains=search) | Q(department__name__icontains=search)
        )

    # Alias diberi awalan n_ agar tidak bentrok dengan nama field (mis. "user")
    agg = Employee.objects.aggregate(
        n_total=Count('id'), **{f'n_{key}': Count('id', filter=f) for key, f in ACCESS_FILTERS.items()},
    )
    counts = {key[2:]: value for key, value in agg.items()}
    page_obj = Paginator(employees, 25).get_page(request.GET.get('page'))
    for emp in page_obj:
        emp.access = employee_access(emp)

    return render(request, 'employees/list.html', {
        'employees':     page_obj,
        'page_obj':      page_obj,
        'search':        search,
        'access_filter': access,
        'counts':        counts,
    })


@role_required(is_ga)
def employee_create(request):
    if request.method == 'POST':
        form = EmployeeForm(request.POST, editor=request.user.employee)
        if form.is_valid():
            emp = form.save()
            _hand_over(request, emp, form.generated_password or form.cleaned_data['password'])
            messages.success(request, f"Karyawan {emp.nama} ditambahkan.")
            return redirect('employee_update', pk=emp.pk)
        flash_form_errors(request, form)
    return render(request, 'employees/create.html', _form_context(request))


@role_required(is_ga)
def employee_update(request, pk):
    emp = get_object_or_404(Employee.objects.select_related('user', 'department'), pk=pk)
    if request.method == 'POST':
        form = EmployeeForm(request.POST, instance=emp, editor=request.user.employee)
        if form.is_valid():
            form.save()
            messages.success(request, f"Data {emp.nama} disimpan.")
            return redirect('employee_update', pk=emp.pk)
        flash_form_errors(request, form)
        emp.refresh_from_db()
    ctx = _form_context(request, emp)
    credential = request.session.pop('new_credential', None)
    ctx.update({
        'new_password': credential['password'] if credential and credential.get('pk') == emp.pk else None,
        'is_self':      emp.pk == request.user.employee.pk,
        'held_count':   emp.held_assets.filter(status='active').count(),
        'has_history':  emp.held_assets.exists(),
    })
    return render(request, 'employees/update.html', ctx)


@require_POST
@role_required(is_ga)
def employee_password(request, pk):
    emp = get_object_or_404(Employee.objects.select_related('user'), pk=pk)
    password = request.POST.get('password', '')
    if password:
        error = check_password_rules(password, emp.user.username)
        if error:
            messages.error(request, error, extra_tags='field:password|Password baru')
            return redirect('employee_update', pk=pk)
    else:
        password = generate_password()
    emp.user.set_password(password)
    emp.user.save(update_fields=['password'])
    if emp.user_id == request.user.pk:
        update_session_auth_hash(request, emp.user)  # tetap masuk setelah mengganti password sendiri
    _hand_over(request, emp, password)
    messages.success(request, f"Password {emp.nama} diganti.")
    return redirect('employee_update', pk=pk)


@require_POST
@role_required(is_ga)
def employee_toggle_active(request, pk):
    emp = get_object_or_404(Employee.objects.select_related('user'), pk=pk)
    if emp.user_id == request.user.pk:
        messages.error(request, "Anda tidak bisa menonaktifkan akun Anda sendiri.")
        return redirect('employee_update', pk=pk)
    emp.user.is_active = not emp.user.is_active
    emp.user.save(update_fields=['is_active'])
    if emp.user.is_active:
        messages.success(request, f"Akun {emp.nama} aktif lagi dan bisa masuk.")
    else:
        messages.success(request, f"Akun {emp.nama} dinonaktifkan. Ia tidak bisa masuk sampai diaktifkan lagi.")
    return redirect('employee_update', pk=pk)


@require_POST
@role_required(is_ga)
def employee_delete(request, pk):
    emp = get_object_or_404(Employee.objects.select_related('user'), pk=pk)
    if emp.user_id == request.user.pk:
        messages.error(request, "Anda tidak bisa menghapus akun Anda sendiri.")
        return redirect('employee_update', pk=pk)
    # Riwayat serah terima ikut terhapus bila karyawan dihapus (CASCADE) — cegah, sarankan nonaktifkan
    if emp.held_assets.exists():
        messages.error(request, f"{emp.nama} punya riwayat pemegang aset, jadi tidak bisa dihapus. Nonaktifkan akunnya saja.")
        return redirect('employee_update', pk=pk)
    nama = emp.nama
    emp.user.delete()  # Employee & profil akses ikut terhapus
    messages.success(request, f"Karyawan {nama} dihapus.")
    return redirect('employee_list')
