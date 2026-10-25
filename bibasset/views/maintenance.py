# views/maintenance.py  [OPTIMIZED]
from datetime import date, timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import role_required
from ..forms import MaintenanceConfigForm, MaintenanceRecordForm, flash_form_errors
from ..models import (
    Asset, MaintenanceSchedule,
    initialize_schedule_for_asset, refresh_overdue_schedules_daily,
)
from ..roles import is_ga


@role_required(is_ga)
def maintenance_create(request, asset_id):
    asset = get_object_or_404(
        Asset.objects.select_related("asset_category", "maintenance_config"),
        id=asset_id
    )
    checklist_hint = asset.asset_category.maintenance_description if asset.asset_category else ""

    if request.method == "POST":
        form = MaintenanceRecordForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                record = form.save(commit=False)
                record.asset        = asset
                record.performed_by = request.user.employee
                record.save()
                schedule_id = request.POST.get("schedule_id")
                if schedule_id and schedule_id.isdigit():
                    # Hanya jadwal milik aset ini yang boleh ditandai selesai
                    MaintenanceSchedule.objects.filter(pk=schedule_id, asset=asset).update(
                        status='done',
                        maintenance_record=record,
                    )
            messages.success(request, "Maintenance berhasil dicatat.")
            return redirect("asset_detail", pk=asset.id)
        flash_form_errors(request, form)

    active_schedule = asset.schedules.filter(
        status__in=['upcoming', 'overdue']
    ).only("id", "scheduled_date", "status").order_by('scheduled_date').first()

    return render(request, "maintenance/create.html", {
        "asset":           asset,
        "checklist_hint":  checklist_hint,
        "active_schedule": active_schedule,
        "today":           date.today(),
        "posted":          request.POST.dict() if request.method == "POST" else None,
    })


@role_required(is_ga)
def maintenance_list(request):
    refresh_overdue_schedules_daily()

    status_filter = request.GET.get('status', '')
    search_query  = request.GET.get('q', '').strip()

    # ── Base queryset — tambahkan maintenance_config ke select_related ──
    schedules_qs = (
        MaintenanceSchedule.objects
        .select_related(
            'asset',
            'asset__asset_category',
            'asset__asset_group',
            'asset__lokasi_asset',
            'asset__maintenance_config',   # ← WAJIB agar tidak N+1 di get_effective_interval_days
        )
        .only(
            'id', 'status', 'scheduled_date', 'notes',
            'asset__id', 'asset__nama_asset', 'asset__asset_number',
            'asset__lifecycle_status',
            'asset__lokasi_asset__name', 'asset__lokasi_asset__full_path',
            'asset__asset_category__name',
            'asset__asset_category__default_interval_value',
            'asset__asset_category__default_interval_unit',
            'asset__asset_group__name',
            'asset__maintenance_config__custom_interval_days',
        )
        .order_by('scheduled_date')
    )

    if status_filter:
        schedules_qs = schedules_qs.filter(status=status_filter)
    else:
        schedules_qs = schedules_qs.filter(status__in=['upcoming', 'overdue'])

    if search_query:
        schedules_qs = schedules_qs.filter(
            Q(asset__nama_asset__icontains=search_query) |
            Q(asset__asset_number__icontains=search_query)
        )

    # ── Stats: 1 query aggregate ──
    schedule_stats = MaintenanceSchedule.objects.aggregate(
        overdue=Count('id', filter=Q(status='overdue')),
        upcoming=Count('id', filter=Q(status='upcoming')),
        done=Count('id', filter=Q(status='done')),
    )

    # ── Pagination ──
    paginator   = Paginator(schedules_qs, 50)
    page_number = request.GET.get('page')
    page_obj    = paginator.get_page(page_number)

    # ── Pre-compute interval & custom flag di Python, bukan di template ──
    # Ini menghilangkan N+1 dari get_effective_interval_days & maintenance_config
    today = date.today()
    for sch in page_obj:
        asset = sch.asset
        config = getattr(asset, 'maintenance_config', None)
        if config:
            sch.interval_days      = config.custom_interval_days
            sch.is_custom_interval = True
        elif asset.asset_category_id:
            # Ambil dari category — sudah di-select_related, tidak query lagi
            sch.interval_days      = asset.asset_category.default_interval_in_days()
            sch.is_custom_interval = False
        else:
            sch.interval_days      = None
            sch.is_custom_interval = False

        # Pre-compute days_remaining juga agar tidak dipanggil 2x di template
        delta = (sch.scheduled_date - today).days
        sch.days_remaining = delta   # negatif kalau overdue

    # ── Modal: all_active_assets — pre-compute maintenance_status juga ──
    # Kalau maintenance_status adalah @property yang query DB, ini N+1 lagi di modal.
    # Solusi: annotate subquery atau cukup hilangkan badge dari modal (badge tidak krusial).
    all_active_assets = (
        Asset.objects
        .filter(lifecycle_status='active')
        .select_related('asset_group', 'asset_category')
        .only('id', 'nama_asset', 'asset_number',
              'asset_group__name', 'asset_category__name')
        .order_by('nama_asset')
    )
    # Pre-compute maint_status untuk modal — 1 query untuk semua aset
    soon_limit = today + timedelta(days=7)
    status_rank = {'overdue': 0, 'upcoming_soon': 1, 'on_track': 2}
    asset_status = {}
    for asset_id, st, sched_date in MaintenanceSchedule.objects.active().values_list(
        'asset_id', 'status', 'scheduled_date'
    ):
        if st == 'overdue':
            cur = 'overdue'
        elif sched_date <= soon_limit:
            cur = 'upcoming_soon'
        else:
            cur = 'on_track'
        prev = asset_status.get(asset_id)
        if prev is None or status_rank[cur] < status_rank[prev]:
            asset_status[asset_id] = cur

    all_active_assets = list(all_active_assets)
    for asset in all_active_assets:
        asset.maint_status = asset_status.get(asset.id)

    context = {
        'page_obj':          page_obj,
        'schedules':         page_obj,
        'status_filter':     status_filter,
        'search_query':      search_query,
        'filtered_count':    paginator.count,
        'overdue_count':     schedule_stats['overdue'],
        'upcoming_count':    schedule_stats['upcoming'],
        'done_count':        schedule_stats['done'],
        'all_active_assets': all_active_assets,
    }
    return render(request, 'maintenance/list.html', context)

@role_required(is_ga)
def maintenance_schedule_skip(request, pk):
    MaintenanceSchedule.objects.active().filter(pk=pk).update(status='skipped')
    return redirect("maintenance_list")


@role_required(is_ga)
def maintenance_config_create(request, asset_id):
    asset = get_object_or_404(
        Asset.objects.select_related("asset_category", "maintenance_config"),
        id=asset_id
    )
    if hasattr(asset, 'maintenance_config'):
        return redirect("maintenance_config_update", asset_id=asset.id)

    category_interval = (
        asset.asset_category.default_interval_in_days() if asset.asset_category else None
    )

    if request.method == "POST":
        form = MaintenanceConfigForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                config = form.save(commit=False)
                config.asset  = asset
                config.set_by = request.user.employee
                config.save()
                asset.maintenance_config = config
                initialize_schedule_for_asset(asset)
            return redirect("asset_detail", pk=asset.id)
        flash_form_errors(request, form)

    return render(request, "maintenance/config_create.html", {
        "asset":             asset,
        "category_interval": category_interval,
    })


@role_required(is_ga)
def maintenance_config_update(request, asset_id):
    asset  = get_object_or_404(Asset.objects.select_related("maintenance_config"), id=asset_id)
    if not hasattr(asset, 'maintenance_config'):
        return redirect("maintenance_config_create", asset_id=asset.id)
    config = asset.maintenance_config

    if request.method == "POST":
        form = MaintenanceConfigForm(request.POST, instance=config)
        if form.is_valid():
            with transaction.atomic():
                form.save()
                initialize_schedule_for_asset(asset)
            return redirect("asset_detail", pk=asset.id)
        flash_form_errors(request, form)
        config.refresh_from_db()

    return render(request, "maintenance/config_update.html", {
        "asset":  asset,
        "config": config,
    })


@role_required(is_ga)
def maintenance_generate_schedule(request, asset_id):
    asset = get_object_or_404(
        Asset.objects.select_related("asset_category", "maintenance_config"),
        id=asset_id
    )
    if not asset.get_effective_interval_days():
        return redirect("maintenance_config_create", asset_id=asset.id)
    # TEKNIK 9: delete() langsung tanpa loop
    asset.schedules.all().delete()
    initialize_schedule_for_asset(asset)
    return redirect("maintenance_list")