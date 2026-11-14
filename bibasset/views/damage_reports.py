# views/damage_reports.py  [OPTIMIZED]

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, Q, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..decorators import role_required
from ..forms import DamageReportForm, ResolveDamageForm, flash_form_errors
from ..models import (
    Asset, AssetAssignment, BeritaAcaraItem, DamageReport, MaintenanceSchedule,
    initialize_schedule_for_asset, pulihkan_status_aktif,
)
from ..roles import can_report_damage, is_ga, is_user_role

SEVERITY_ORDER = ['critical', 'high', 'medium', 'low']


# ================================================
# USER DASHBOARD
# ================================================

@role_required(is_user_role)
def user_dashboard(request):
    employee = request.user.employee

    active_assignments = list(AssetAssignment.objects.filter(
        diserahkan_kepada=employee, status='active'
    ).select_related(
        'aset', 'aset__kategori_aset', 'aset__lokasi'
    ).only(
        'id', 'tanggal_serah', 'catatan',
        'aset__id', 'aset__nama_aset', 'aset__nomor_aset', 'aset__status_siklus_hidup',
        'aset__kategori_aset__nama', 'aset__lokasi__nama', 'aset__lokasi__jalur_lengkap',
    ).order_by('aset__nama_aset'))

    # Aset bersama yang diserahkan atas nama departemen karyawan ini
    shared_assignments = list(AssetAssignment.objects.filter(
        departemen_id=employee.departemen_id, status='active'
    ).select_related('aset', 'aset__lokasi').order_by('aset__nama_aset')) if employee.departemen_id else []

    my_reports_qs = DamageReport.objects.filter(
        dilaporkan_oleh=employee
    ).select_related('aset').only(
        'id', 'judul', 'tingkat_keparahan', 'status_laporan', 'dilaporkan_pada', 'catatan_ga',
        'aset__nama_aset', 'aset__nomor_aset',
    ).order_by('-dilaporkan_pada')

    counts = my_reports_qs.aggregate(
        total=Count('id'), active=Count('id', filter=Q(status_laporan__in=DamageReport.ACTIVE_STATUS)),
    )
    context = {
        'active_assignments': active_assignments,
        'my_reports':         my_reports_qs[:6],
        'pending_count':      counts['active'],
        'total_reports':      counts['total'],
        'total_held':         len(active_assignments),
        'shared_assignments': shared_assignments,
    }
    return render(request, 'user/dashboard.html', context)


# ================================================
# USER: DAMAGE REPORT
# ================================================

def _report_block_reason(asset):
    """Alasan aset tidak bisa dilaporkan, atau None bila bisa."""
    if asset.status_siklus_hidup == 'writeoff':
        return "Aset ini sudah di-write off."
    active = DamageReport.objects.filter(aset=asset, status_laporan__in=DamageReport.ACTIVE_STATUS).only('id').first()
    if active:
        return f"Sudah dilaporkan (#{active.pk}) dan sedang ditangani GA."
    if asset.status_siklus_hidup == 'maintenance':
        return "Aset ini sedang dalam maintenance oleh GA."
    return None


@role_required(can_report_damage)
def damage_report_create(request, aset_id=None):
    # Karyawan boleh melapor aset mana pun yang ia temukan rusak — tidak harus aset yang ia pegang —
    # supaya kerusakan cepat sampai ke GA tanpa menunggu serah terima.
    employee = request.user.employee
    held_assets = Asset.objects.filter(
        assignments__diserahkan_kepada=employee,
        assignments__status='active',
        status_siklus_hidup='active',
    ).exclude(
        damage_reports__status_laporan__in=DamageReport.ACTIVE_STATUS,
    ).select_related('lokasi').only(
        'id', 'nama_aset', 'nomor_aset', 'lokasi__nama', 'lokasi__jalur_lengkap',
    ).distinct().order_by('nama_aset')

    asset_qs = Asset.objects.select_related('kategori_aset', 'lokasi')
    selected_asset = None
    form = None
    if aset_id:
        selected_asset = get_object_or_404(asset_qs, id=aset_id)
        reason = _report_block_reason(selected_asset)
        if reason:
            messages.warning(request, f"{selected_asset.nama_aset} tidak perlu dilaporkan lagi: {reason}")
            return redirect('damage_report_create')

    if request.method == 'POST':
        asset_id_post = request.POST.get('aset_id') or aset_id
        if not str(asset_id_post or '').isdigit():
            messages.error(request, "Pilih aset yang rusak dulu.")
            return render(request, 'user/damage_report_form.html', {
                'held_assets': held_assets, 'posted': request.POST.dict(), 'severities': DamageReport.SEVERITY,
            })
        asset = get_object_or_404(asset_qs, id=asset_id_post)
        reason = _report_block_reason(asset)
        if reason:
            messages.warning(request, f"{asset.nama_aset} tidak perlu dilaporkan lagi: {reason}")
            return redirect('damage_report_create')

        form = DamageReportForm(request.POST, request.FILES)
        if form.is_valid():
            report = form.save(commit=False)
            report.aset       = asset
            report.dilaporkan_oleh = employee
            report.save()   # atomic: ubah status aset + jadwal corrective sekaligus
            messages.success(request, f"Laporan kerusakan '{asset.nama_aset}' terkirim ke GA.")
            return redirect('user_damage_report_detail', pk=report.pk)
        selected_asset = asset  # tetap terpilih saat isian lain perlu diperbaiki
        messages.error(request, "Laporan belum terkirim. Periksa isian yang ditandai.")

    return render(request, 'user/damage_report_form.html', {
        'held_assets':    held_assets,
        'selected_asset': selected_asset,
        'form':           form,
        'posted':         request.POST.dict() if request.method == 'POST' else None,
        'severities':     DamageReport.SEVERITY,
    })


@role_required(can_report_damage)
def damage_report_asset_search(request):
    """Cari aset untuk dilaporkan (aset siapa pun). Maksimal 10 hasil, beserta bisa/tidaknya dilaporkan."""
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})
    qs = Asset.objects.exclude(status_siklus_hidup='writeoff')
    # Setiap kata harus cocok di salah satu kolom: "ac mess 3" → AC di Mess 3
    for term in q.split()[:5]:
        qs = qs.filter(Q(nama_aset__icontains=term) | Q(nomor_aset__icontains=term) |
                       Q(lokasi__jalur_lengkap__icontains=term) | Q(kategori_aset__nama__icontains=term))
    qs = qs.select_related('lokasi', 'kategori_aset').order_by('nama_aset', 'id')[:10]
    assets = list(qs)
    reported = dict(DamageReport.objects.filter(
        aset__in=assets, status_laporan__in=DamageReport.ACTIVE_STATUS,
    ).values_list('aset_id', 'id'))
    mine = set(AssetAssignment.objects.filter(
        aset__in=assets, diserahkan_kepada=request.user.employee, status='active',
    ).values_list('aset_id', flat=True))

    def reason(a):
        if a.pk in reported:
            return f"Sudah dilaporkan (#{reported[a.pk]}), sedang ditangani GA"
        if a.status_siklus_hidup == 'maintenance':
            return "Sedang maintenance"
        return ''

    return JsonResponse({'results': [{
        'id':       a.pk,
        'name':     a.nama_aset,
        'number':   a.nomor_aset,
        'category': a.kategori_aset.nama if a.kategori_aset else '',
        'location': a.lokasi.jalur_lengkap if a.lokasi else 'Belum ditempatkan',
        'tag':      'Aset Anda' if a.pk in mine else '',
        'blocked':  reason(a),
    } for a in assets]})


@role_required(can_report_damage)
def user_damage_report_detail(request, pk):
    report = get_object_or_404(
        DamageReport.objects.select_related('aset', 'aset__lokasi', 'dilaporkan_oleh', 'ditangani_oleh').only(
            'id', 'judul', 'deskripsi', 'tingkat_keparahan', 'status_laporan',
            'dilaporkan_pada', 'diselesaikan_pada', 'catatan_ga', 'foto_bukti',
            'aset__nama_aset', 'aset__nomor_aset', 'aset__lokasi__nama',
            'dilaporkan_oleh__nama', 'ditangani_oleh__nama',
        ),
        pk=pk, dilaporkan_oleh=request.user.employee
    )
    return render(request, 'user/damage_report_detail.html', {'report': report})


@role_required(can_report_damage)
def user_damage_report_list(request):
    reports = DamageReport.objects.filter(
        dilaporkan_oleh=request.user.employee
    ).select_related('aset').only(
        'id', 'judul', 'tingkat_keparahan', 'status_laporan', 'dilaporkan_pada', 'aset__nama_aset'
    ).order_by('-dilaporkan_pada')

    # TEKNIK 3: 1 query agregat untuk summary counts
    counts = reports.aggregate(
        active=Count('id', filter=Q(status_laporan__in=DamageReport.ACTIVE_STATUS)),
        resolved=Count('id', filter=Q(status_laporan='resolved')),
    )

    return render(request, 'user/damage_report_list.html', {
        'reports':        reports,
        'active_count':   counts['active'],
        'resolved_count': counts['resolved'],
    })


# ================================================
# GA: DAMAGE REPORT MANAGEMENT
# ================================================

@role_required(is_ga)
def ga_damage_report_list(request):
    status_filter   = request.GET.get('status', '')
    severity_filter = request.GET.get('severity', '')

    reports = DamageReport.objects.select_related(
        'aset', 'aset__kategori_aset', 'aset__lokasi',
        'dilaporkan_oleh', 'dilaporkan_oleh__departemen', 'ditangani_oleh'
    ).only(
        'id', 'judul', 'deskripsi', 'tingkat_keparahan', 'status_laporan', 'dilaporkan_pada',
        'aset__id', 'aset__nama_aset', 'aset__nomor_aset',
        'aset__kategori_aset__nama', 'aset__lokasi__nama',
        'dilaporkan_oleh__nama', 'dilaporkan_oleh__departemen__nama', 'ditangani_oleh__nama',
    )

    if status_filter:
        # Arsip (selesai/ditolak) dibaca dari yang terbaru
        reports = reports.filter(status_laporan=status_filter).order_by('-dilaporkan_pada')
    else:
        # Antrean aktif: yang paling berbahaya dan belum disentuh GA tampil duluan
        reports = reports.filter(status_laporan__in=DamageReport.ACTIVE_STATUS).annotate(
            sev_rank=Case(*[When(tingkat_keparahan=s, then=i) for i, s in enumerate(SEVERITY_ORDER)], default=9),
            pending_first=Case(When(status_laporan='pending', then=0), default=1),
        ).order_by('sev_rank', 'pending_first', '-dilaporkan_pada')

    if severity_filter:
        reports = reports.filter(tingkat_keparahan=severity_filter)

    # TEKNIK 3: Semua badge count dalam 1 query
    all_counts = DamageReport.objects.aggregate(
        pending=Count('id', filter=Q(status_laporan='pending')),
        acknowledged=Count('id', filter=Q(status_laporan='acknowledged')),
        in_progress=Count('id', filter=Q(status_laporan='in_progress')),
        resolved=Count('id', filter=Q(status_laporan='resolved')),
        rejected=Count('id', filter=Q(status_laporan='rejected')),
        critical=Count('id', filter=Q(
            tingkat_keparahan='critical',
            status_laporan__in=DamageReport.ACTIVE_STATUS
        )),
    )

    page_obj = Paginator(reports, 25).get_page(request.GET.get('page'))
    context = {
        'reports':            page_obj,
        'page_obj':           page_obj,
        'status_filter':      status_filter,
        'severity_filter':    severity_filter,
        'active_count':       all_counts['pending'] + all_counts['acknowledged'] + all_counts['in_progress'],
        'pending_count':      all_counts['pending'],
        'acknowledged_count': all_counts['acknowledged'],
        'in_progress_count':  all_counts['in_progress'],
        'resolved_count':     all_counts['resolved'],
        'rejected_count':     all_counts['rejected'],
        'critical_count':     all_counts['critical'],
    }
    return render(request, 'ga/damage_report_list.html', context)


def _render_ga_detail(request, pk, resolve_posted=None, status=200):
    report = get_object_or_404(
        DamageReport.objects.select_related(
            'aset', 'aset__kategori_aset', 'aset__lokasi', 'dilaporkan_oleh',
            'ditangani_oleh', 'maintenance_penyelesai', 'maintenance_penyelesai__dilaksanakan_oleh'
        ),
        pk=pk,
    )
    corrective_schedule = report.corrective_schedules.only(
        'id', 'status', 'tanggal_jadwal'
    ).first()
    # Pelapor tidak harus pemegang aset; GA perlu tahu siapa pemegangnya
    holder = AssetAssignment.objects.filter(
        aset=report.aset, status='active',
    ).select_related('diserahkan_kepada', 'departemen').first()

    return render(request, 'ga/damage_report_detail.html', {
        'report':              report,
        'corrective_schedule': corrective_schedule,
        'holder':              holder,
        'asset':               report.aset,
        'today':               timezone.localdate(),
        'ba_item':             report.ba_items.select_related('ba').order_by('-ba__dibuat_pada').first(),
        # Isian form "Catat perbaikan selesai" yang gagal validasi, diisi ulang di template
        'resolve_posted':      resolve_posted or {},
    }, status=status)


@role_required(is_ga)
def ga_damage_report_detail(request, pk):
    return _render_ga_detail(request, pk)


@role_required(is_ga)
@require_POST
def ga_damage_report_acknowledge(request, pk):
    # TEKNIK 9: update() langsung tanpa fetch + save
    updated = DamageReport.objects.filter(pk=pk, status_laporan='pending').update(
        status_laporan='acknowledged',
        ditangani_oleh=request.user.employee,
        catatan_ga=request.POST.get('catatan_ga', ''),
    )
    if updated:
        messages.success(request, f"Laporan #{pk} sudah diakui. Status: Diterima GA.")
    return redirect('ga_damage_report_detail', pk=pk)


@role_required(is_ga)
@require_POST
def ga_damage_report_resolve(request, pk):
    report = get_object_or_404(
        DamageReport.objects.select_related('aset'),
        pk=pk
    )
    if report.status_laporan not in DamageReport.ACTIVE_STATUS:
        messages.warning(request, f"Laporan #{pk} sudah {report.get_report_status_display().lower()}.")
        return redirect('ga_damage_report_detail', pk=pk)

    form = ResolveDamageForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            record = form.save(commit=False)
            record.aset            = report.aset
            record.jenis_maintenance = 'corrective'
            record.dilaksanakan_oleh     = request.user.employee
            record.deskripsi_pekerjaan = (
                f"[Dari Laporan Kerusakan #{report.pk}]\n"
                f"Judul: {report.judul}\n"
                f"Deskripsi awal: {report.deskripsi}\n\n"
                f"Tindakan perbaikan: {record.deskripsi_pekerjaan}"
            )
            record.save()

            DamageReport.objects.filter(pk=pk).update(
                status_laporan='resolved',
                ditangani_oleh=request.user.employee,
                catatan_ga=request.POST.get('catatan_ga', ''),
                maintenance_penyelesai=record,
                diselesaikan_pada=timezone.now(),
            )
            report.corrective_schedules.filter(status='overdue').update(
                status='done', catatan_maintenance=record,
            )
            # record.save() di atas belum bisa mengaktifkan aset karena laporan ini masih berjalan;
            # sekarang sudah resolved → aktif bila tidak ada laporan lain
            pulihkan_status_aktif(report.aset_id)
        messages.success(request, f"Laporan #{pk} diselesaikan dan tercatat sebagai maintenance corrective.")
        return redirect('ga_damage_report_detail', pk=pk)

    # Tampilkan lagi halaman detail dengan isian yang sudah diketik; error ditempel di samping isiannya
    flash_form_errors(request, form)
    return _render_ga_detail(request, pk, resolve_posted=request.POST.dict(), status=422)


@role_required(is_ga)
@require_POST
def ga_damage_report_reject(request, pk):
    report = get_object_or_404(
        DamageReport.objects.select_related('aset').only(
            'id', 'status_laporan', 'aset__id', 'aset__status_siklus_hidup'
        ),
        pk=pk
    )
    if request.method == 'POST':
        with transaction.atomic():
            DamageReport.objects.filter(pk=pk).update(
                status_laporan='rejected',
                ditangani_oleh=request.user.employee,
                catatan_ga=request.POST.get('catatan_ga', 'Laporan ditolak.'),
            )
            # Jadwal corrective & temuan di draf BA dari laporan yang ditolak tidak relevan lagi
            BeritaAcaraItem.objects.filter(laporan_kerusakan_id=pk, sumber='laporan', ba__status='draft').delete()
            MaintenanceSchedule.objects.filter(laporan_kerusakan_id=pk).exclude(status='done').delete()

            # Tidak ada laporan lain yang berjalan → aset aktif lagi & jadwal preventive dibuat ulang
            if pulihkan_status_aktif(report.aset_id):
                initialize_schedule_for_asset(report.aset)

        messages.warning(request, f"Laporan #{pk} ditolak.")
    return redirect('ga_damage_report_list')