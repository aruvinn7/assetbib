# views/damage_reports.py  [OPTIMIZED]

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, Q, When
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..decorators import role_required
from ..forms import DamageReportForm, ResolveDamageForm, flash_form_errors
from ..models import (
    Asset, AssetAssignment, BeritaAcaraItem, DamageReport, MaintenanceSchedule,
    initialize_schedule_for_asset, pulihkan_status_aktif,
)
from ..roles import is_ga, is_user_role

SEVERITY_ORDER = ['critical', 'high', 'medium', 'low']


# ================================================
# USER DASHBOARD
# ================================================

@role_required(is_user_role)
def user_dashboard(request):
    employee = request.user.employee

    active_assignments = list(AssetAssignment.objects.filter(
        assigned_to=employee, status='active'
    ).select_related(
        'asset', 'asset__asset_category', 'asset__lokasi_asset'
    ).only(
        'id', 'assigned_date', 'notes',
        'asset__id', 'asset__nama_asset', 'asset__asset_number', 'asset__lifecycle_status',
        'asset__asset_category__name', 'asset__lokasi_asset__name', 'asset__lokasi_asset__full_path',
    ).order_by('asset__nama_asset'))

    my_reports_qs = DamageReport.objects.filter(
        reported_by=employee
    ).select_related('asset').only(
        'id', 'title', 'severity', 'report_status', 'reported_at', 'ga_notes',
        'asset__nama_asset', 'asset__asset_number',
    ).order_by('-reported_at')

    counts = my_reports_qs.aggregate(
        total=Count('id'), active=Count('id', filter=Q(report_status__in=DamageReport.ACTIVE_STATUS)),
    )
    context = {
        'active_assignments': active_assignments,
        'my_reports':         my_reports_qs[:6],
        'pending_count':      counts['active'],
        'total_reports':      counts['total'],
        'total_held':         len(active_assignments),
    }
    return render(request, 'user/dashboard.html', context)


# ================================================
# USER: DAMAGE REPORT
# ================================================

@role_required(is_user_role)
def damage_report_create(request, asset_id=None):
    employee = request.user.employee
    held_assets = Asset.objects.filter(
        assignments__assigned_to=employee,
        assignments__status='active',
        lifecycle_status='active',
    ).select_related('lokasi_asset').only(
        'id', 'nama_asset', 'asset_number', 'lokasi_asset__name',
    ).distinct()

    selected_asset = None
    form = None
    if asset_id:
        selected_asset = get_object_or_404(
            Asset.objects.select_related('asset_category', 'lokasi_asset').only(
                'id', 'nama_asset', 'asset_number', 'asset_category__name', 'lokasi_asset__name',
            ),
            id=asset_id,
        )
        if not held_assets.filter(id=asset_id).exists():
            messages.error(request, "Aset ini tidak bisa dilaporkan: bukan aset yang Anda pegang, atau sudah berstatus Maintenance.")
            return redirect('user_dashboard')

    if request.method == 'POST':
        asset_id_post = request.POST.get('asset_id') or asset_id
        asset         = get_object_or_404(Asset.objects.only('id', 'nama_asset'), id=asset_id_post)

        # User hanya boleh melapor aset yang sedang ia pegang
        if not held_assets.filter(id=asset.id).exists():
            messages.error(request, "Aset ini tidak bisa dilaporkan: bukan aset yang Anda pegang, atau sudah berstatus Maintenance.")
            return redirect('user_dashboard')

        active_report = DamageReport.objects.filter(
            asset=asset, report_status__in=DamageReport.ACTIVE_STATUS,
        ).only('id').first()

        if active_report:
            messages.warning(
                request,
                f"Aset '{asset.nama_asset}' sudah memiliki laporan aktif (#{active_report.pk})."
            )
            return redirect('user_dashboard')

        form = DamageReportForm(request.POST, request.FILES)
        if form.is_valid():
            report = form.save(commit=False)
            report.asset       = asset
            report.reported_by = employee
            report.save()   # atomic: ubah status aset + jadwal corrective sekaligus
            messages.success(request, f"Laporan kerusakan '{asset.nama_asset}' terkirim ke GA.")
            return redirect('user_damage_report_detail', pk=report.pk)
        messages.error(request, "Laporan belum terkirim. Periksa isian yang ditandai.")

    return render(request, 'user/damage_report_form.html', {
        'held_assets':    held_assets,
        'selected_asset': selected_asset,
        'form':           form,
        'posted':         request.POST.dict() if request.method == 'POST' else None,
        'severities':     DamageReport.SEVERITY,
    })


@role_required(is_user_role)
def user_damage_report_detail(request, pk):
    report = get_object_or_404(
        DamageReport.objects.select_related('asset', 'asset__lokasi_asset', 'reported_by', 'handled_by').only(
            'id', 'title', 'description', 'severity', 'report_status',
            'reported_at', 'resolved_at', 'ga_notes', 'photo_evidence',
            'asset__nama_asset', 'asset__asset_number', 'asset__lokasi_asset__name',
            'reported_by__nama', 'handled_by__nama',
        ),
        pk=pk, reported_by=request.user.employee
    )
    return render(request, 'user/damage_report_detail.html', {'report': report})


@role_required(is_user_role)
def user_damage_report_list(request):
    reports = DamageReport.objects.filter(
        reported_by=request.user.employee
    ).select_related('asset').only(
        'id', 'title', 'severity', 'report_status', 'reported_at', 'asset__nama_asset'
    ).order_by('-reported_at')

    # TEKNIK 3: 1 query agregat untuk summary counts
    counts = reports.aggregate(
        active=Count('id', filter=Q(report_status__in=DamageReport.ACTIVE_STATUS)),
        resolved=Count('id', filter=Q(report_status='resolved')),
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
        'asset', 'asset__asset_category', 'asset__lokasi_asset',
        'reported_by', 'reported_by__department', 'handled_by'
    ).only(
        'id', 'title', 'description', 'severity', 'report_status', 'reported_at',
        'asset__id', 'asset__nama_asset', 'asset__asset_number',
        'asset__asset_category__name', 'asset__lokasi_asset__name',
        'reported_by__nama', 'reported_by__department__name', 'handled_by__nama',
    )

    if status_filter:
        # Arsip (selesai/ditolak) dibaca dari yang terbaru
        reports = reports.filter(report_status=status_filter).order_by('-reported_at')
    else:
        # Antrean aktif: yang paling berbahaya dan belum disentuh GA tampil duluan
        reports = reports.filter(report_status__in=DamageReport.ACTIVE_STATUS).annotate(
            sev_rank=Case(*[When(severity=s, then=i) for i, s in enumerate(SEVERITY_ORDER)], default=9),
            pending_first=Case(When(report_status='pending', then=0), default=1),
        ).order_by('sev_rank', 'pending_first', '-reported_at')

    if severity_filter:
        reports = reports.filter(severity=severity_filter)

    # TEKNIK 3: Semua badge count dalam 1 query
    all_counts = DamageReport.objects.aggregate(
        pending=Count('id', filter=Q(report_status='pending')),
        acknowledged=Count('id', filter=Q(report_status='acknowledged')),
        in_progress=Count('id', filter=Q(report_status='in_progress')),
        resolved=Count('id', filter=Q(report_status='resolved')),
        rejected=Count('id', filter=Q(report_status='rejected')),
        critical=Count('id', filter=Q(
            severity='critical',
            report_status__in=DamageReport.ACTIVE_STATUS
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
            'asset', 'asset__asset_category', 'asset__lokasi_asset', 'reported_by',
            'handled_by', 'resolved_maintenance', 'resolved_maintenance__performed_by'
        ),
        pk=pk,
    )
    corrective_schedule = report.corrective_schedules.only(
        'id', 'status', 'scheduled_date'
    ).first()

    return render(request, 'ga/damage_report_detail.html', {
        'report':              report,
        'corrective_schedule': corrective_schedule,
        'asset':               report.asset,
        'today':               timezone.localdate(),
        'ba_item':             report.ba_items.select_related('ba').order_by('-ba__created_at').first(),
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
    updated = DamageReport.objects.filter(pk=pk, report_status='pending').update(
        report_status='acknowledged',
        handled_by=request.user.employee,
        ga_notes=request.POST.get('ga_notes', ''),
    )
    if updated:
        messages.success(request, f"Laporan #{pk} sudah diakui. Status: Diterima GA.")
    return redirect('ga_damage_report_detail', pk=pk)


@role_required(is_ga)
@require_POST
def ga_damage_report_resolve(request, pk):
    report = get_object_or_404(
        DamageReport.objects.select_related('asset'),
        pk=pk
    )
    if report.report_status not in DamageReport.ACTIVE_STATUS:
        messages.warning(request, f"Laporan #{pk} sudah {report.get_report_status_display().lower()}.")
        return redirect('ga_damage_report_detail', pk=pk)

    form = ResolveDamageForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            record = form.save(commit=False)
            record.asset            = report.asset
            record.maintenance_type = 'corrective'
            record.performed_by     = request.user.employee
            record.deskripsi_pekerjaan = (
                f"[Dari Laporan Kerusakan #{report.pk}]\n"
                f"Judul: {report.title}\n"
                f"Deskripsi awal: {report.description}\n\n"
                f"Tindakan perbaikan: {record.deskripsi_pekerjaan}"
            )
            record.save()

            DamageReport.objects.filter(pk=pk).update(
                report_status='resolved',
                handled_by=request.user.employee,
                ga_notes=request.POST.get('ga_notes', ''),
                resolved_maintenance=record,
                resolved_at=timezone.now(),
            )
            report.corrective_schedules.filter(status='overdue').update(
                status='done', maintenance_record=record,
            )
            # record.save() di atas belum bisa mengaktifkan aset karena laporan ini masih berjalan;
            # sekarang sudah resolved → aktif bila tidak ada laporan lain
            pulihkan_status_aktif(report.asset_id)
        messages.success(request, f"Laporan #{pk} diselesaikan dan tercatat sebagai maintenance corrective.")
        return redirect('ga_damage_report_detail', pk=pk)

    # Tampilkan lagi halaman detail dengan isian yang sudah diketik; error ditempel di samping isiannya
    flash_form_errors(request, form)
    return _render_ga_detail(request, pk, resolve_posted=request.POST.dict(), status=422)


@role_required(is_ga)
@require_POST
def ga_damage_report_reject(request, pk):
    report = get_object_or_404(
        DamageReport.objects.select_related('asset').only(
            'id', 'report_status', 'asset__id', 'asset__lifecycle_status'
        ),
        pk=pk
    )
    if request.method == 'POST':
        with transaction.atomic():
            DamageReport.objects.filter(pk=pk).update(
                report_status='rejected',
                handled_by=request.user.employee,
                ga_notes=request.POST.get('ga_notes', 'Laporan ditolak.'),
            )
            # Jadwal corrective & temuan di draf BA dari laporan yang ditolak tidak relevan lagi
            BeritaAcaraItem.objects.filter(damage_report_id=pk, sumber='laporan', ba__status='draft').delete()
            MaintenanceSchedule.objects.filter(damage_report_id=pk).exclude(status='done').delete()

            # Tidak ada laporan lain yang berjalan → aset aktif lagi & jadwal preventive dibuat ulang
            if pulihkan_status_aktif(report.asset_id):
                initialize_schedule_for_asset(report.asset)

        messages.warning(request, f"Laporan #{pk} ditolak.")
    return redirect('ga_damage_report_list')