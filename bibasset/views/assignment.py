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
    return (Asset.objects.exclude(status_siklus_hidup='writeoff').exclude(assignments__status='active')
            .select_related('lokasi', 'departemen_pemilik', 'kategori_aset'))


@role_required(is_ga)
def assignment_list(request):
    tab = request.GET.get('tab', 'pribadi')
    if tab not in TABS:
        tab = 'pribadi'
    search = request.GET.get('q', '').strip()

    active = AssetAssignment.objects.filter(status='active')
    free = _unassigned_assets()
    counts = active.aggregate(pribadi=Count('id', filter=Q(diserahkan_kepada__isnull=False)),
                              departemen=Count('id', filter=Q(departemen__isnull=False)))
    counts['tersedia'] = free.filter(lokasi__tempat_penyimpanan=True).count()
    counts['belum'] = free.filter(status_siklus_hidup='active').exclude(lokasi__tempat_penyimpanan=True).count()

    if tab in ('pribadi', 'departemen'):
        rows = active.filter(diserahkan_kepada__isnull=(tab == 'departemen')).select_related(
            'aset', 'aset__lokasi', 'diserahkan_kepada__departemen', 'departemen', 'diserahkan_oleh',
        ).order_by('-tanggal_serah', '-id')
        if search:
            rows = rows.filter(
                Q(aset__nama_aset__icontains=search) | Q(aset__nomor_aset__icontains=search) |
                Q(diserahkan_kepada__nama__icontains=search) | Q(diserahkan_kepada__departemen__nama__icontains=search) |
                Q(departemen__nama__icontains=search) | Q(aset__lokasi__jalur_lengkap__icontains=search)
            )
    else:
        rows = free.filter(lokasi__tempat_penyimpanan=True) if tab == 'tersedia' else \
               free.filter(status_siklus_hidup='active').exclude(lokasi__tempat_penyimpanan=True)
        rows = rows.order_by('lokasi__jalur_lengkap', 'nama_aset') if tab == 'tersedia' else rows.order_by('-id')
        if search:
            rows = rows.filter(
                Q(nama_aset__icontains=search) | Q(nomor_aset__icontains=search) |
                Q(lokasi__jalur_lengkap__icontains=search) | Q(departemen_pemilik__nama__icontains=search)
            )

    page_obj = Paginator(rows, 25).get_page(request.GET.get('page'))
    return render(request, 'ga/assignment_list.html', {
        'tab': tab, 'counts': counts, 'search': search,
        'rows': page_obj, 'page_obj': page_obj,
    })


@role_required(is_ga)
def assignment_create(request, aset_id=None):
    selected_asset = get_object_or_404(Asset, id=aset_id) if aset_id else None
    form = AssignmentForm(request.POST or None)

    if request.method == 'POST':
        if form.is_valid():
            d        = form.cleaned_data
            asset    = d['aset_id']
            tanggal  = d['tanggal_serah'] or timezone.localdate()
            # Movement baru + serah terima (penerima lama otomatis 'transferred') dalam 1 jalur
            if d['mode'] == 'personal':
                emp = d['diserahkan_kepada']
                record_movement(
                    asset, dipegang_oleh=emp, departemen=emp.departemen or asset.departemen_pemilik,
                    lokasi=d['lokasi'], dicatat_oleh=request.user.employee,
                    catatan=f"Serah terima ke {emp.nama}", tanggal=tanggal, catatan_pemegang=d['catatan'],
                )
                recipient = emp.nama
            else:
                dept = d['departemen']
                record_movement(
                    asset, dipegang_oleh=None, departemen=dept, lokasi=d['lokasi'],
                    dicatat_oleh=request.user.employee, catatan=f"Serah terima ke departemen {dept.nama}",
                    tanggal=tanggal, catatan_pemegang=d['catatan'], shared=True,
                )
                # Departemen penerima = departemen yang meminta/mengadakan → jadi pemilik aset
                if asset.departemen_pemilik_id != dept.pk:
                    Asset.objects.filter(pk=asset.pk).update(departemen_pemilik=dept)
                recipient = f"departemen {dept.nama}"
            messages.success(request, f"Aset '{asset.nama_aset}' diserahkan ke {recipient}.")
            return _to_list('pribadi' if d['mode'] == 'personal' else 'departemen')
        flash_form_errors(request, form)

    return render(request, 'ga/assignment_create.html', {
        # lokasi & kategori di-join — dulu 950+ query karena lazy load per aset
        # Penerima saat ini ikut dimuat supaya form bisa memberi tahu bila aset akan dipindahtangankan
        'assets': Asset.objects.filter(status_siklus_hidup='active').select_related(
            'kategori_aset', 'lokasi',
        ).annotate(holder_name=Subquery(
            AssetAssignment.objects.filter(aset=OuterRef('pk'), status='active')
            .annotate(nama=Coalesce('diserahkan_kepada__nama', 'departemen__nama')).values('nama')[:1]
        )).order_by('nama_aset'),
        'employees':      Employee.objects.filter(user_profile__isnull=False, user__is_active=True)
                          .select_related('departemen').order_by('nama'),
        'departments':    Department.objects.order_by('nama'),
        'locations':      Location.objects.filter(tempat_penyimpanan=False).order_by('jalur_lengkap'),
        'selected_asset': selected_asset,
        'posted':         request.POST.dict() if request.method == 'POST' else None,
    })


def _record_return_damage(asset, condition, notes, by):
    """Kondisi rusak saat dikembalikan → laporan kerusakan (sudah diterima GA). None bila sudah ada laporan aktif."""
    if DamageReport.objects.filter(aset=asset, status_laporan__in=DamageReport.ACTIVE_STATUS).exists():
        return None
    return DamageReport.objects.create(
        aset=asset, dilaporkan_oleh=by, ditangani_oleh=by, status_laporan='acknowledged',
        judul='Rusak saat dikembalikan', deskripsi=notes,
        tingkat_keparahan='low' if condition == 'rusak_ringan' else 'high',
    )


@role_required(is_ga)
def assignment_return(request, pk):
    assignment = get_object_or_404(
        AssetAssignment.objects.select_related('aset__lokasi', 'aset__kategori_aset',
                                               'diserahkan_kepada__departemen', 'departemen'),
        pk=pk, status='active',
    )
    asset = assignment.aset
    storage = Location.storage_for(asset.lokasi)
    form = AssignmentReturnForm(request.POST or None)

    if request.method == 'POST':
        if form.is_valid():
            d = form.cleaned_data
            by = request.user.employee
            # Serah terima ditutup ('returned') oleh record_movement → sync_holder
            record_movement(
                asset, dipegang_oleh=None, departemen=asset.departemen_pemilik, lokasi=d['lokasi'],
                dicatat_oleh=by, catatan=f"Dikembalikan oleh {assignment.recipient_name}",
                tanggal=d['tanggal_kembali'] or timezone.localdate(), catatan_pemegang=d['catatan'], release=True,
            )
            AssetAssignment.objects.filter(pk=assignment.pk).update(kondisi_kembali=d['kondisi'])

            msg = f"'{asset.nama_aset}' sudah dikembalikan oleh {assignment.recipient_name} dan disimpan di {d['lokasi'].nama}."
            if d['kondisi'] != 'baik':
                report = _record_return_damage(asset, d['kondisi'], d['catatan'], by)
                msg += f" Laporan kerusakan #{report.pk} dibuat." if report else " Aset ini sudah punya laporan kerusakan aktif."
            messages.success(request, msg)
            return _to_list('departemen' if assignment.is_shared else 'pribadi')
        flash_form_errors(request, form)

    return render(request, 'ga/assignment_return.html', {
        'assignment': assignment,
        'asset':      asset,
        'storage':    storage,
        'locations':  Location.objects.order_by('-tempat_penyimpanan', 'jalur_lengkap'),
        'conditions': AssetAssignment.CONDITION,
        'posted':     request.POST.dict() if request.method == 'POST' else None,
    })
