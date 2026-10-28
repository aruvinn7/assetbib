# views/assignment.py
# Serah terima aset. Barang yang ikut orangnya (kategori "bisa diserahkan pribadi") diserahkan ke
# karyawan; selain itu atas nama departemen yang meminta/mengadakan. Saat dikembalikan, aset masuk
# tempat titip GA (atau lokasi lain yang dipilih) dan kondisinya diperiksa.

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from ..decorators import role_required
from ..forms import AssignmentForm, AssignmentReturnForm, flash_form_errors
from ..models import (
    Asset, AssetAssignment, DamageReport, Department, Employee, Location, record_movement,
)
from ..roles import is_ga

# Tab daftar: dua berisi serah terima aktif, dua berisi aset yang belum diserahkan
TABS = ('pribadi', 'departemen', 'tersedia', 'belum')


def _to_list(tab):
    return redirect(f"{reverse('assignment_list')}?tab={tab}")


def _unassigned_assets():
    return (Asset.objects.exclude(lifecycle_status='writeoff').exclude(assignments__status='active')
            .select_related('lokasi_asset', 'owned_by_department', 'asset_category'))


@role_required(is_ga)
def assignment_list(request):
    tab = request.GET.get('tab', 'pribadi')
    if tab not in TABS:
        tab = 'pribadi'
    search = request.GET.get('q', '').strip()

    active = AssetAssignment.objects.filter(status='active')
    free = _unassigned_assets()
    counts = active.aggregate(pribadi=Count('id', filter=Q(assigned_to__isnull=False)),
                              departemen=Count('id', filter=Q(department__isnull=False)))
    counts['tersedia'] = free.filter(lokasi_asset__is_storage=True).count()
    counts['belum'] = free.filter(lifecycle_status='active').exclude(lokasi_asset__is_storage=True).count()

    if tab in ('pribadi', 'departemen'):
        rows = active.filter(assigned_to__isnull=(tab == 'departemen')).select_related(
            'asset', 'asset__lokasi_asset', 'assigned_to__department', 'department', 'assigned_by',
        ).order_by('-assigned_date', '-id')
        if search:
            rows = rows.filter(
                Q(asset__nama_asset__icontains=search) | Q(asset__asset_number__icontains=search) |
                Q(assigned_to__nama__icontains=search) | Q(assigned_to__department__name__icontains=search) |
                Q(department__name__icontains=search) | Q(asset__lokasi_asset__full_path__icontains=search)
            )
    else:
        rows = free.filter(lokasi_asset__is_storage=True) if tab == 'tersedia' else \
               free.filter(lifecycle_status='active').exclude(lokasi_asset__is_storage=True)
        rows = rows.order_by('lokasi_asset__full_path', 'nama_asset') if tab == 'tersedia' else rows.order_by('-id')
        if search:
            rows = rows.filter(
                Q(nama_asset__icontains=search) | Q(asset_number__icontains=search) |
                Q(lokasi_asset__full_path__icontains=search) | Q(owned_by_department__name__icontains=search)
            )

    page_obj = Paginator(rows, 25).get_page(request.GET.get('page'))
    return render(request, 'ga/assignment_list.html', {
        'tab': tab, 'counts': counts, 'search': search,
        'rows': page_obj, 'page_obj': page_obj,
    })


@role_required(is_ga)
def assignment_create(request, asset_id=None):
    selected_asset = get_object_or_404(Asset, id=asset_id) if asset_id else None
    form = AssignmentForm(request.POST or None)

    if request.method == 'POST':
        if form.is_valid():
            d        = form.cleaned_data
            asset    = d['asset_id']
            tanggal  = d['assigned_date'] or timezone.localdate()
            # Movement baru + serah terima (penerima lama otomatis 'transferred') dalam 1 jalur
            if d['mode'] == 'personal':
                emp = d['assigned_to']
                record_movement(
                    asset, held_by=emp, department=emp.department or asset.owned_by_department,
                    location=d['location'], recorded_by=request.user.employee,
                    notes=f"Serah terima ke {emp.nama}", tanggal=tanggal, holder_notes=d['notes'],
                )
                recipient = emp.nama
            else:
                dept = d['department']
                record_movement(
                    asset, held_by=None, department=dept, location=d['location'],
                    recorded_by=request.user.employee, notes=f"Serah terima ke departemen {dept.name}",
                    tanggal=tanggal, holder_notes=d['notes'], shared=True,
                )
                # Departemen penerima = departemen yang meminta/mengadakan → jadi pemilik aset
                if asset.owned_by_department_id != dept.pk:
                    Asset.objects.filter(pk=asset.pk).update(owned_by_department=dept)
                recipient = f"departemen {dept.name}"
            messages.success(request, f"Aset '{asset.nama_asset}' diserahkan ke {recipient}.")
            return _to_list('pribadi' if d['mode'] == 'personal' else 'departemen')
        flash_form_errors(request, form)

    return render(request, 'ga/assignment_create.html', {
        # lokasi & kategori di-join — dulu 950+ query karena lazy load per aset
        # Penerima saat ini ikut dimuat supaya form bisa memberi tahu bila aset akan dipindahtangankan
        'assets': Asset.objects.filter(lifecycle_status='active').select_related(
            'asset_category', 'lokasi_asset',
        ).annotate(holder_name=Subquery(
            AssetAssignment.objects.filter(asset=OuterRef('pk'), status='active')
            .annotate(name=Coalesce('assigned_to__nama', 'department__name')).values('name')[:1]
        )).order_by('nama_asset'),
        'employees':      Employee.objects.filter(user_profile__isnull=False, user__is_active=True)
                          .select_related('department').order_by('nama'),
        'departments':    Department.objects.order_by('name'),
        'locations':      Location.objects.filter(is_storage=False).order_by('full_path'),
        'selected_asset': selected_asset,
        'posted':         request.POST.dict() if request.method == 'POST' else None,
    })


def _record_return_damage(asset, condition, notes, by):
    """Kondisi rusak saat dikembalikan → laporan kerusakan (sudah diterima GA). None bila sudah ada laporan aktif."""
    if DamageReport.objects.filter(asset=asset, report_status__in=DamageReport.ACTIVE_STATUS).exists():
        return None
    return DamageReport.objects.create(
        asset=asset, reported_by=by, handled_by=by, report_status='acknowledged',
        title='Rusak saat dikembalikan', description=notes,
        severity='low' if condition == 'rusak_ringan' else 'high',
    )


@role_required(is_ga)
def assignment_return(request, pk):
    assignment = get_object_or_404(
        AssetAssignment.objects.select_related('asset__lokasi_asset', 'asset__asset_category',
                                               'assigned_to__department', 'department'),
        pk=pk, status='active',
    )
    asset = assignment.asset
    storage = Location.storage_for(asset.lokasi_asset)
    form = AssignmentReturnForm(request.POST or None)

    if request.method == 'POST':
        if form.is_valid():
            d = form.cleaned_data
            by = request.user.employee
            # Serah terima ditutup ('returned') oleh record_movement → sync_holder
            record_movement(
                asset, held_by=None, department=asset.owned_by_department, location=d['location'],
                recorded_by=by, notes=f"Dikembalikan oleh {assignment.recipient_name}",
                tanggal=d['returned_date'] or timezone.localdate(), holder_notes=d['notes'], release=True,
            )
            AssetAssignment.objects.filter(pk=assignment.pk).update(return_condition=d['condition'])

            msg = f"'{asset.nama_asset}' sudah dikembalikan oleh {assignment.recipient_name} dan disimpan di {d['location'].name}."
            if d['condition'] != 'baik':
                report = _record_return_damage(asset, d['condition'], d['notes'], by)
                msg += f" Laporan kerusakan #{report.pk} dibuat." if report else " Aset ini sudah punya laporan kerusakan aktif."
            messages.success(request, msg)
            return _to_list('departemen' if assignment.is_shared else 'pribadi')
        flash_form_errors(request, form)

    return render(request, 'ga/assignment_return.html', {
        'assignment': assignment,
        'asset':      asset,
        'storage':    storage,
        'locations':  Location.objects.order_by('-is_storage', 'full_path'),
        'conditions': AssetAssignment.CONDITION,
        'posted':     request.POST.dict() if request.method == 'POST' else None,
    })
