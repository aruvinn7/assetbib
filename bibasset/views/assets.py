# views/assets.py [OPTIMIZED + PAGINATION]
import os
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, IntegerField, OuterRef, Prefetch, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from ..decorators import role_required
from ..forms import AssetForm, AssetUpdateForm, flash_form_errors
from ..models import (
    Asset, AssetComponent, AssetPhoto, AssetGroup, AssetCategory,
    Department, Location, MaintenanceRecord, MaintenanceSchedule,
    compute_health, initialize_schedule_for_asset,
)
from ..roles import is_ga
from .utils import is_ga_or_finance


@role_required(is_ga_or_finance)
def asset_list(request):
    status_filter    = request.GET.get('status', '')
    financial_filter = request.GET.get('financial', '')
    health_filter    = request.GET.get('health', '')
    search_query     = request.GET.get('q', '').strip()
    category_filter  = request.GET.get('category', '')
    group_filter     = request.GET.get('group', '')
    location_filter  = request.GET.get('location', '')

    # ── Query utama: health dihitung dari annotate, tanpa prefetch record ──
    component_count = Subquery(
        AssetComponent.objects.filter(asset=OuterRef('pk'))
        .order_by().values('asset').annotate(c=Count('id')).values('c')[:1],
        output_field=IntegerField(),
    )
    assets_qs = (
        Asset.objects
        .select_related("lokasi_asset", "owned_by_department", "asset_group", "financial")
        .with_health_annotations()
        .annotate(component_count=Coalesce(component_count, 0))
        .order_by('id')   # urutan sama seperti sebelumnya; Paginator butuh urutan pasti
    )

    if search_query:
        assets_qs = assets_qs.filter(
            Q(nama_asset__icontains=search_query)   |
            Q(asset_number__icontains=search_query) |
            Q(no_po__icontains=search_query)        |
            Q(asset_category__name__icontains=search_query) |
            Q(asset_group__name__icontains=search_query)    |
            Q(lokasi_asset__name__icontains=search_query)   |
            Q(owned_by_department__name__icontains=search_query)
        )

    if category_filter.isdigit():
        assets_qs = assets_qs.filter(asset_category_id=category_filter)
    if group_filter.isdigit():
        assets_qs = assets_qs.filter(asset_group_id=group_filter)
    if location_filter.isdigit():
        assets_qs = assets_qs.filter(lokasi_asset_id=location_filter)

    if status_filter in ['active', 'maintenance', 'writeoff']:
        assets_qs = assets_qs.filter(lifecycle_status=status_filter)
    elif financial_filter == 'no':
        assets_qs = assets_qs.filter(financial__isnull=True)

    # Jumlah critical dihitung di SQL — tidak perlu load semua aset ke memori
    critical_qs = assets_qs.critical_health()
    if health_filter == 'critical':
        assets_qs = critical_qs

    stats = Asset.objects.dashboard_stats()

    # ── Pagination di level queryset: hanya 50 baris yang diambil ──
    paginator = Paginator(assets_qs, 50)
    page_obj  = paginator.get_page(request.GET.get('page'))
    for asset in page_obj:
        nilai_awal = asset.financial.nilai_awal_aset if hasattr(asset, 'financial') else None
        asset.health, asset.ratio = compute_health(
            asset.total_biaya_annot, asset.jumlah_corrective, nilai_awal
        )

    context = {
        "assets":                page_obj,
        "page_obj":              page_obj,
        "total_asset":           stats['total'],
        "active_count":          stats['active'],
        "maintenance_count":     stats['in_maintenance'],
        "writeoff_count":        stats['writeoff'],
        "unvalued_count":        stats['unvalued'],
        "critical_health_count": paginator.count if health_filter == 'critical' else critical_qs.count(),
        "status_filter":         status_filter,
        "financial_filter":      financial_filter,
        "health_filter":         health_filter,
        "search_query":          search_query,
        "filtered_count":        paginator.count,
        "category_filter":       category_filter,
        "group_filter":          group_filter,
        "location_filter":       location_filter,
    }
    return render(request, "asset/list.html", context)


def _asset_form_context():
    return {
        "locations":    Location.objects.only("id", "name", "full_path").order_by("full_path"),
        "departments":  Department.objects.only("id", "name").order_by("name"),
        "asset_groups": AssetGroup.objects.prefetch_related(
            Prefetch("categories", queryset=AssetCategory.objects.order_by("name"))
        ).order_by("name"),
    }


@role_required(is_ga_or_finance)
def asset_create(request):
    if request.method == "POST":
        form = AssetForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                asset = form.save(commit=False)
                asset.created_by = request.user.employee
                asset.save()
                initialize_schedule_for_asset(asset)
            messages.success(request, f"Aset '{asset.nama_asset}' berhasil ditambahkan.")
            return redirect("asset_list")
        flash_form_errors(request, form)

    context = _asset_form_context()
    if request.method == "POST":
        context["posted"] = request.POST.dict()
    return render(request, "asset/create.html", context)


@role_required(is_ga_or_finance)
def asset_update(request, pk):
    asset = get_object_or_404(
        Asset.objects.select_related("asset_category", "maintenance_config", "lokasi_asset"), pk=pk
    )
    if request.method == "POST":
        form = AssetUpdateForm(request.POST, instance=asset)
        if form.is_valid():
            form.save()
            messages.success(request, f"Aset '{asset.nama_asset}' berhasil diperbarui.")
            return redirect("asset_detail", pk=asset.pk)
        flash_form_errors(request, form)
        asset.refresh_from_db()

    records = list(
        asset.maintenance_records
        .order_by('-tanggal_pelaksanaan')
        .only("id", "asset_id", "tanggal_pelaksanaan", "maintenance_type", "hasil", "deskripsi_pekerjaan")
    )
    context = {
        **_asset_form_context(),
        "asset":               asset,
        "maintenance_records": records,
        "next_schedule":       asset.get_next_schedule(),
        "last_maintenance":    records[0] if records else None,
        "effective_interval":  asset.get_effective_interval_days(),
    }
    if request.method == "POST":
        context["posted"] = request.POST.dict()
    return render(request, "asset/update.html", context)


@role_required(is_ga_or_finance)
def asset_detail(request, pk):
    asset = get_object_or_404(
        Asset.objects.select_related(
            "lokasi_asset", "owned_by_department", "asset_group",
            "asset_category", "financial", "maintenance_config",
        ).prefetch_related(
            Prefetch("photos", queryset=AssetPhoto.objects.order_by('-is_primary', '-uploaded_at')),
            Prefetch(
                "maintenance_records",
                queryset=MaintenanceRecord.objects.select_related("performed_by")
                    .order_by('-tanggal_pelaksanaan'),
            ),
            Prefetch("schedules", queryset=MaintenanceSchedule.objects.order_by('scheduled_date')),
            "components",
        ),
        pk=pk,
    )

    stats = asset.maintenance_records.aggregate(
        total_count=Count('id'),
        corrective_count=Count('id', filter=Q(maintenance_type='corrective')),
        preventive_count=Count('id', filter=Q(maintenance_type='preventive')),
        inspection_count=Count('id', filter=Q(maintenance_type='inspection')),
        total_biaya=Sum('biaya'),
        total_biaya_corrective=Sum('biaya', filter=Q(maintenance_type='corrective')),
        total_biaya_preventive=Sum('biaya', filter=~Q(maintenance_type='corrective')),
        avg_biaya_corrective=Avg('biaya', filter=Q(maintenance_type='corrective')),
        major_count=Count('id', filter=Q(maintenance_type='corrective', hasil='major_issue')),
        minor_count=Count('id', filter=Q(maintenance_type='corrective', hasil='minor_issue')),
        replaced_count=Count('id', filter=Q(maintenance_type='corrective', hasil='replaced_component')),
        ok_count=Count('id', filter=Q(maintenance_type='corrective', hasil='ok')),
    )

    # Semua dari data yang sudah di-prefetch — tidak query ulang
    all_records      = asset.maintenance_records.all()
    schedules        = asset.schedules.all()
    photos           = asset.photos.all()
    corrective_count = stats['corrective_count'] or 0
    total_biaya      = stats['total_biaya'] or Decimal('0')

    # Health dihitung sekali dari angka aggregate di atas
    health = asset.health_report(total_biaya=total_biaya, breakdown=corrective_count)

    def pct(n):
        return round((n or 0) / corrective_count * 100) if corrective_count else 0

    context = {
        "asset":                asset,
        "maintenance_records":  all_records,
        "corrective_records":   [r for r in all_records if r.maintenance_type == 'corrective'],
        "schedules":            schedules,
        "components":           asset.components.all(),
        "photos":               photos,
        "primary_photo":        next((p for p in photos if p.is_primary), None),
        "next_schedule":        next((s for s in schedules if s.status == 'upcoming'), None),
        "last_maintenance":     all_records[0] if all_records else None,
        "effective_interval":   asset.get_effective_interval_days(),
        "all_maintenance_count":  stats['total_count'] or 0,
        "corrective_count":       corrective_count,
        "preventive_count":       stats['preventive_count'] or 0,
        "inspection_count":       stats['inspection_count'] or 0,
        "total_biaya_all":        total_biaya,
        "total_biaya_corrective": stats['total_biaya_corrective'] or Decimal('0'),
        "total_biaya_preventive": stats['total_biaya_preventive'] or Decimal('0'),
        "avg_biaya_corrective":   stats['avg_biaya_corrective'] or Decimal('0'),
        "major_count":    stats['major_count'] or 0,
        "minor_count":    stats['minor_count'] or 0,
        "replaced_count": stats['replaced_count'] or 0,
        "ok_count":       stats['ok_count'] or 0,
        "major_pct":      pct(stats['major_count']),
        "minor_pct":      pct(stats['minor_count']),
        "replaced_pct":   pct(stats['replaced_count']),
        "ok_pct":         pct(stats['ok_count']),
        "health_status":          health['status'],
        "repair_cost_ratio":      health['ratio'],
        "repair_recommendations": health['recommendations'],
        "threshold":              health['threshold'],
    }
    return render(request, "asset/detail.html", context)


@role_required(is_ga)
def asset_delete(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    back_params = request.GET.urlencode()  # otomatis berisi page, q, status, dll
    asset.delete()
    redirect_url = reverse('asset_list')
    if back_params:
        redirect_url += '?' + back_params
    return redirect(redirect_url)

@role_required(is_ga)
def photo_upload(request, asset_id):
    asset = get_object_or_404(Asset.objects.only("id"), id=asset_id)
    if request.method == "POST":
        photo_file = request.FILES.get('photo')
        if photo_file and photo_file.size <= 5 * 1024 * 1024:
            AssetPhoto.objects.create(
                asset=asset, photo=photo_file,
                photo_type=request.POST.get('photo_type', 'unit'),
                caption=request.POST.get('caption', ''),
                is_primary=request.POST.get('is_primary') == 'on',
                uploaded_by=request.user.employee,
            )
            if not AssetPhoto.objects.filter(asset=asset, is_primary=True).exists():
                first_id = AssetPhoto.objects.filter(asset=asset).values_list('id', flat=True).first()
                if first_id:
                    AssetPhoto.objects.filter(id=first_id).update(is_primary=True)
    return redirect('asset_detail', pk=asset.id)


@role_required(is_ga)
@require_POST
def photo_set_primary(request, photo_id):
    photo = get_object_or_404(AssetPhoto.objects.only("id", "asset_id"), id=photo_id)
    AssetPhoto.objects.filter(asset_id=photo.asset_id).update(is_primary=False)
    AssetPhoto.objects.filter(id=photo_id).update(is_primary=True)
    return redirect('asset_detail', pk=photo.asset_id)


@role_required(is_ga)
@require_POST
def photo_delete(request, photo_id):
    photo = get_object_or_404(AssetPhoto.objects.only("id", "asset_id", "photo"), id=photo_id)
    asset_id = photo.asset_id
    if photo.photo and os.path.isfile(photo.photo.path):
        os.remove(photo.photo.path)
    photo.delete()
    remaining = AssetPhoto.objects.filter(asset_id=asset_id)
    if remaining.exists() and not remaining.filter(is_primary=True).exists():
        first_id = remaining.values_list('id', flat=True).first()
        if first_id:
            AssetPhoto.objects.filter(id=first_id).update(is_primary=True)
    return redirect('asset_detail', pk=asset_id)