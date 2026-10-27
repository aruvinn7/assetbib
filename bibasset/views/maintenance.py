# views/maintenance.py  [OPTIMIZED]
import re

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone

from ..decorators import role_required
from ..forms import MaintenanceConfigForm, MaintenanceRecordForm, flash_form_errors
from ..models import (
    Asset, MaintenanceRecord, MaintenanceSchedule,
    initialize_schedule_for_asset, refresh_overdue_schedules_daily,
)
from ..roles import is_ga


@role_required(is_ga)
def maintenance_create(request, asset_id):
    asset = get_object_or_404(
        Asset.objects.select_related("asset_category", "maintenance_config"),
        id=asset_id
    )
    if asset.lifecycle_status == 'writeoff':
        # Aset write off tidak dirawat lagi; dialog cepat ikut diarahkan ke detail aset (res.redirected)
        messages.error(request, f"Aset '{asset.nama_asset}' sudah di-write off, maintenance tidak bisa dicatat.")
        return redirect("asset_detail", pk=asset.id)
    checklist_hint = asset.asset_category.maintenance_description if asset.asset_category else ""
    # Dipanggil dari dialog "Catat cepat" (fetch) → balas parsial/JSON, bukan halaman penuh
    is_dialog = request.headers.get("X-Requested-With") == "fetch"
    form = None

    if request.method == "POST":
        form = MaintenanceRecordForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                schedule_id = request.POST.get("schedule_id")
                # Hanya jadwal milik aset ini yang boleh ditandai selesai. Ditandai SEBELUM record
                # disimpan: record.save() menghapus jadwal 'upcoming' lama saat membuat jadwal baru,
                # sehingga jadwal yang dikerjakan dulu ikut terhapus dan riwayatnya hilang.
                done_qs = MaintenanceSchedule.objects.filter(pk=schedule_id, asset=asset) \
                    if schedule_id and schedule_id.isdigit() else MaintenanceSchedule.objects.none()
                done_qs.update(status='done')
                record = form.save(commit=False)
                record.asset        = asset
                record.performed_by = request.user.employee
                record.save()
                done_qs.update(maintenance_record=record)
            if is_dialog:
                nxt = asset.schedules.filter(status='upcoming').order_by('scheduled_date').first()
                return JsonResponse({
                    "ok": True,
                    "message": f"Maintenance {asset.asset_number} dicatat."
                               + (f" Jadwal berikutnya {nxt.scheduled_date:%d %b %Y}." if nxt else ""),
                })
            messages.success(request, "Maintenance berhasil dicatat.")
            return redirect("asset_detail", pk=asset.id)
        if not is_dialog:
            flash_form_errors(request, form)

    active_schedule = asset.schedules.active().only(
        "id", "scheduled_date", "status", "damage_report"
    ).order_by('scheduled_date').first()

    # Deskripsi yang pernah dipakai untuk aset ini → tombol isi cepat
    recent = []
    for d in asset.maintenance_records.order_by('-tanggal_pelaksanaan').values_list('deskripsi_pekerjaan', flat=True)[:25]:
        if d not in recent:
            recent.append(d)
    default_type = ''
    if active_schedule:
        default_type = 'corrective' if active_schedule.damage_report_id else 'preventive'
    # Dialog & halaman penuh memakai isian yang sama (maintenance/_fields.html)
    return render(request, "maintenance/_quick_form.html" if is_dialog else "maintenance/create.html", {
        "asset":           asset,
        "form":            form,
        "posted":          request.POST.dict() if request.method == "POST" else None,
        # Nomor di awal baris dibuang; <ol> sudah menomori
        "checklist_lines": [re.sub(r'^\s*\d+\s*[.)]\s*', '', l).strip() for l in checklist_hint.splitlines() if l.strip()],
        "active_schedule": active_schedule,
        "today":           timezone.localdate(),
        "recent":          recent[:3],
        "default_type":    default_type,
        "interval":        asset.get_effective_interval_days(),
    }, status=422 if form is not None else 200)


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
            'asset__maintenance_config__custom_interval_days',
        )
        .order_by('scheduled_date')
    )

    if status_filter:
        schedules_qs = schedules_qs.filter(status=status_filter)
    else:
        schedules_qs = schedules_qs.active()

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

    # ── Interval & custom flag per baris — relasi sudah di-select_related, jadi 0 query ──
    for sch in page_obj:
        sch.interval_days      = sch.asset.get_effective_interval_days()
        sch.is_custom_interval = hasattr(sch.asset, 'maintenance_config')

    context = {
        'page_obj':          page_obj,
        'schedules':         page_obj,
        'status_filter':     status_filter,
        'search_query':      search_query,
        'filtered_count':    paginator.count,
        'overdue_count':     schedule_stats['overdue'],
        'upcoming_count':    schedule_stats['upcoming'],
        'done_count':        schedule_stats['done'],
    }
    return render(request, 'maintenance/list.html', context)

@role_required(is_ga)
@require_POST
def maintenance_schedule_skip(request, pk):
    sch = MaintenanceSchedule.objects.active().select_related('asset').filter(pk=pk).first()
    if sch:
        MaintenanceSchedule.objects.filter(pk=pk).update(status='skipped')
        messages.success(request, f"Jadwal {sch.asset.nama_asset} ({sch.scheduled_date:%d %b %Y}) dilewati.")
    next_url = request.POST.get('next', '')
    return redirect(next_url if next_url.startswith('/maintenance/') else 'maintenance_list')


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
            messages.success(request, f"Interval khusus {config.custom_interval_days} hari disimpan; jadwal berikutnya dihitung ulang.")
            return redirect("asset_detail", pk=asset.id)
        flash_form_errors(request, form)

    return render(request, "maintenance/config_form.html", {
        "asset":             asset,
        "config":            None,
        "category_interval": category_interval,
        "posted":            request.POST.dict() if request.method == "POST" else None,
    })


@role_required(is_ga)
def maintenance_config_update(request, asset_id):
    asset  = get_object_or_404(Asset.objects.select_related("asset_category", "maintenance_config"), id=asset_id)
    if not hasattr(asset, 'maintenance_config'):
        return redirect("maintenance_config_create", asset_id=asset.id)
    config = asset.maintenance_config

    if request.method == "POST":
        form = MaintenanceConfigForm(request.POST, instance=config)
        if form.is_valid():
            with transaction.atomic():
                form.save()
                initialize_schedule_for_asset(asset)
            messages.success(request, f"Interval khusus diubah menjadi {config.custom_interval_days} hari; jadwal berikutnya dihitung ulang.")
            return redirect("asset_detail", pk=asset.id)
        flash_form_errors(request, form)
        config.refresh_from_db()

    return render(request, "maintenance/config_form.html", {
        "asset":             asset,
        "config":            config,
        "category_interval": asset.asset_category.default_interval_in_days() if asset.asset_category else None,
        "posted":            request.POST.dict() if request.method == "POST" else None,
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