# views/damage_reports.py  [OPTIMIZED]

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..decorators import role_required
from ..forms import DamageReportForm, ResolveDamageForm, flash_form_errors
from ..models import (
    Asset, AssetAssignment, DamageReport, MaintenanceSchedule,
    initialize_schedule_for_asset,
)
from ..roles import is_ga, is_user_role

ACTIVE_REPORT_STATUS = ['pending', 'acknowledged', 'in_progress']


# ================================================
# USER DASHBOARD
# ================================================

@role_required(is_user_role)
def user_dashboard(request):
    employee = request.user.employee

    active_assignments = AssetAssignment.objects.filter(
        assigned_to=employee, status='active'
    ).select_related(
        'asset', 'asset__asset_category', 'asset__lokasi_asset'
    ).only(
        'id', 'assigned_date', 'notes',
        'asset__id', 'asset__nama_asset', 'asset__lifecycle_status',
        'asset__asset_category__name', 'asset__lokasi_asset__name',
    )

    my_reports_qs = DamageReport.objects.filter(
        reported_by=employee
    ).select_related('asset').only(
        'id', 'title', 'severity', 'report_status', 'reported_at',
        'asset__nama_asset',
    ).order_by('-reported_at')

    # TEKNIK 3: 1 query untuk pending count
    pending_count = my_reports_qs.filter(report_status__in=ACTIVE_REPORT_STATUS).count()

    context = {
        'active_assignments': active_assignments,
        'my_reports':         my_reports_qs[:10],
        'pending_count':      pending_count,
        'total_held':         active_assignments.count(),
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
    ).only('id', 'nama_asset').distinct()

    selected_asset = None
    if asset_id:
        selected_asset = get_object_or_404(Asset.objects.only('id', 'nama_asset'), id=asset_id)
        if not held_assets.filter(id=asset_id).exists():
            messages.error(request, "Anda tidak memegang aset ini atau aset sedang dalam perbaikan.")
            return redirect('user_dashboard')

    if request.method == 'POST':
        asset_id_post = request.POST.get('asset_id') or asset_id
        asset         = get_object_or_404(Asset.objects.only('id', 'nama_asset'), id=asset_id_post)

        # User hanya boleh melapor aset yang sedang ia pegang
        if not held_assets.filter(id=asset.id).exists():
            messages.error(request, "Anda tidak memegang aset ini atau aset sedang dalam perbaikan.")
            return redirect('user_dashboard')

        active_report = DamageReport.objects.filter(
            asset=asset, report_status__in=ACTIVE_REPORT_STATUS,
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
            messages.success(request, f"✅ Laporan kerusakan '{asset.nama_asset}' berhasil dikirim.")
            return redirect('user_damage_report_detail', pk=report.pk)
        flash_form_errors(request, form)

    return render(request, 'user/damage_report_form.html', {
        'held_assets':    held_assets,
        'selected_asset': selected_asset,
        'posted':         request.POST.dict() if request.method == 'POST' else None,
    })


@role_required(is_user_role)
def user_damage_report_detail(request, pk):
    report = get_object_or_404(
        DamageReport.objects.select_related('asset').only(
            'id', 'title', 'description', 'severity', 'report_status',
            'reported_at', 'ga_notes', 'photo_evidence',
            'asset__nama_asset',
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
        pending=Count('id', filter=Q(report_status='pending')),
        resolved=Count('id', filter=Q(report_status='resolved')),
    )

    return render(request, 'user/damage_report_list.html', {
        'reports':        reports,
        'pending_count':  counts['pending'],
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
        'reported_by', 'handled_by'
    ).only(
        'id', 'title', 'severity', 'report_status', 'reported_at',
        'asset__nama_asset', 'asset__asset_category__name', 'asset__lokasi_asset__name',
        'reported_by__nama', 'handled_by__nama',
    ).order_by('-reported_at')

    if status_filter:
        reports = reports.filter(report_status=status_filter)
    else:
        reports = reports.filter(report_status__in=ACTIVE_REPORT_STATUS)

    if severity_filter:
        reports = reports.filter(severity=severity_filter)

    # TEKNIK 3: Semua badge count dalam 1 query
    all_counts = DamageReport.objects.aggregate(
        pending=Count('id', filter=Q(report_status='pending')),
        acknowledged=Count('id', filter=Q(report_status='acknowledged')),
        in_progress=Count('id', filter=Q(report_status='in_progress')),
        resolved=Count('id', filter=Q(report_status='resolved')),
        critical=Count('id', filter=Q(
            severity='critical',
            report_status__in=ACTIVE_REPORT_STATUS
        )),
    )

    context = {
        'reports':            reports,
        'status_filter':      status_filter,
        'severity_filter':    severity_filter,
        'pending_count':      all_counts['pending'],
        'acknowledged_count': all_counts['acknowledged'],
        'in_progress_count':  all_counts['in_progress'],
        'resolved_count':     all_counts['resolved'],
        'critical_count':     all_counts['critical'],
    }
    return render(request, 'ga/damage_report_list.html', context)


@role_required(is_ga)
def ga_damage_report_detail(request, pk):
    report = get_object_or_404(
        DamageReport.objects.select_related(
            'asset', 'asset__asset_category', 'reported_by',
            'handled_by', 'resolved_maintenance'
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
    })


@role_required(is_ga)
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
def ga_damage_report_resolve(request, pk):
    report = get_object_or_404(
        DamageReport.objects.select_related('asset'),
        pk=pk
    )
    if request.method == 'POST':
        if report.report_status not in ACTIVE_REPORT_STATUS:
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
            messages.success(request, f"✅ Laporan #{pk} diselesaikan.")
            return redirect('ga_damage_report_detail', pk=pk)
        flash_form_errors(request, form)

    # Form penyelesaian ada di halaman detail laporan; kesalahan ditampilkan di sana
    return redirect('ga_damage_report_detail', pk=pk)


@role_required(is_ga)
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
            # Jadwal corrective dari laporan yang ditolak tidak relevan lagi
            MaintenanceSchedule.objects.filter(damage_report_id=pk).exclude(status='done').delete()

            other_active = DamageReport.objects.filter(
                asset=report.asset, report_status__in=ACTIVE_REPORT_STATUS,
            ).exclude(pk=pk).exists()

            if not other_active:
                Asset.objects.filter(pk=report.asset_id).update(lifecycle_status='active')
                initialize_schedule_for_asset(report.asset)

        messages.warning(request, f"Laporan #{pk} ditolak.")
    return redirect('ga_damage_report_list')