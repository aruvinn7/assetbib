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
def maintenance_create(request, aset_id):
    asset = get_object_or_404(
        Asset.objects.select_related("kategori_aset", "maintenance_config"),
        id=aset_id
    )
    if asset.status_siklus_hidup == 'writeoff':
        # Aset write off tidak dirawat lagi; dialog cepat ikut diarahkan ke detail aset (res.redirected)
        messages.error(request, f"Aset '{asset.nama_aset}' sudah di-write off, maintenance tidak bisa dicatat.")
        return redirect("asset_detail", pk=asset.id)
    checklist_hint = asset.kategori_aset.deskripsi_maintenance if asset.kategori_aset else ""
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
                done_qs = MaintenanceSchedule.objects.filter(pk=schedule_id, aset=asset) \
                    if schedule_id and schedule_id.isdigit() else MaintenanceSchedule.objects.none()
                done_qs.update(status='done')
                record = form.save(commit=False)
                record.aset        = asset
                record.dilaksanakan_oleh = request.user.employee
                record.save()
                done_qs.update(catatan_maintenance=record)
            if is_dialog:
                nxt = asset.schedules.filter(status='upcoming').order_by('tanggal_jadwal').first()
                return JsonResponse({
                    "ok": True,
                    "message": f"Maintenance {asset.nomor_aset} dicatat."
                               + (f" Jadwal berikutnya {nxt.tanggal_jadwal:%d %b %Y}." if nxt else ""),
                })
            messages.success(request, "Maintenance berhasil dicatat.")
            return redirect("asset_detail", pk=asset.id)
        if not is_dialog:
            flash_form_errors(request, form)

    active_schedule = asset.schedules.active().only(
        "id", "tanggal_jadwal", "status", "laporan_kerusakan"
    ).order_by('tanggal_jadwal').first()

    # Deskripsi yang pernah dipakai untuk aset ini → tombol isi cepat
    recent = []
    for d in asset.maintenance_records.order_by('-tanggal_pelaksanaan').values_list('deskripsi_pekerjaan', flat=True)[:25]:
        if d not in recent:
            recent.append(d)
    default_type = ''
    if active_schedule:
        default_type = 'corrective' if active_schedule.laporan_kerusakan_id else 'preventive'
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
            'aset',
            'aset__kategori_aset',
            'aset__lokasi',
            'aset__maintenance_config',   # ← WAJIB agar tidak N+1 di get_effective_interval_days
        )
        .only(
            'id', 'status', 'tanggal_jadwal', 'catatan',
            'aset__id', 'aset__nama_aset', 'aset__nomor_aset',
            'aset__status_siklus_hidup',
            'aset__lokasi__nama', 'aset__lokasi__jalur_lengkap',
            'aset__kategori_aset__nama',
            'aset__kategori_aset__interval_default',
            'aset__kategori_aset__satuan_interval_default',
            'aset__maintenance_config__interval_khusus_hari',
        )
        .order_by('tanggal_jadwal')
    )

    if status_filter:
        schedules_qs = schedules_qs.filter(status=status_filter)
    else:
        schedules_qs = schedules_qs.active()

    if search_query:
        schedules_qs = schedules_qs.filter(
            Q(aset__nama_aset__icontains=search_query) |
            Q(aset__nomor_aset__icontains=search_query)
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
        sch.interval_days      = sch.aset.get_effective_interval_days()
        sch.is_custom_interval = hasattr(sch.aset, 'maintenance_config')

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
    sch = MaintenanceSchedule.objects.active().select_related('aset').filter(pk=pk).first()
    if sch:
        MaintenanceSchedule.objects.filter(pk=pk).update(status='skipped')
        messages.success(request, f"Jadwal {sch.aset.nama_aset} ({sch.tanggal_jadwal:%d %b %Y}) dilewati.")
    next_url = request.POST.get('next', '')
    return redirect(next_url if next_url.startswith('/maintenance/') else 'maintenance_list')


@role_required(is_ga)
def maintenance_config_create(request, aset_id):
    asset = get_object_or_404(
        Asset.objects.select_related("kategori_aset", "maintenance_config"),
        id=aset_id
    )
    if hasattr(asset, 'maintenance_config'):
        return redirect("maintenance_config_update", aset_id=asset.id)

    category_interval = (
        asset.kategori_aset.default_interval_in_days() if asset.kategori_aset else None
    )

    if request.method == "POST":
        form = MaintenanceConfigForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                config = form.save(commit=False)
                config.aset  = asset
                config.ditetapkan_oleh = request.user.employee
                config.save()
                asset.maintenance_config = config
                initialize_schedule_for_asset(asset)
            messages.success(request, f"Interval khusus {config.interval_khusus_hari} hari disimpan; jadwal berikutnya dihitung ulang.")
            return redirect("asset_detail", pk=asset.id)
        flash_form_errors(request, form)

    return render(request, "maintenance/config_form.html", {
        "asset":             asset,
        "config":            None,
        "category_interval": category_interval,
        "posted":            request.POST.dict() if request.method == "POST" else None,
    })


@role_required(is_ga)
def maintenance_config_update(request, aset_id):
    aset  = get_object_or_404(Asset.objects.select_related("kategori_aset", "maintenance_config"), id=aset_id)
    if not hasattr(aset, 'maintenance_config'):
        return redirect("maintenance_config_create", aset_id=aset.id)
    config = aset.maintenance_config

    if request.method == "POST":
        form = MaintenanceConfigForm(request.POST, instance=config)
        if form.is_valid():
            with transaction.atomic():
                form.save()
                initialize_schedule_for_asset(aset)
            messages.success(request, f"Interval khusus diubah menjadi {config.interval_khusus_hari} hari; jadwal berikutnya dihitung ulang.")
            return redirect("asset_detail", pk=aset.id)
        flash_form_errors(request, form)
        config.refresh_from_db()

    return render(request, "maintenance/config_form.html", {
        "asset":             aset,
        "config":            config,
        "category_interval": aset.kategori_aset.default_interval_in_days() if aset.kategori_aset else None,
        "posted":            request.POST.dict() if request.method == "POST" else None,
    })


@role_required(is_ga)
def maintenance_generate_schedule(request, aset_id):
    asset = get_object_or_404(
        Asset.objects.select_related("kategori_aset", "maintenance_config"),
        id=aset_id
    )
    if not asset.get_effective_interval_days():
        return redirect("maintenance_config_create", aset_id=asset.id)
    # TEKNIK 9: delete() langsung tanpa loop
    asset.schedules.all().delete()
    initialize_schedule_for_asset(asset)
    return redirect("maintenance_list")