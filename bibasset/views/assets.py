# views/assets.py [OPTIMIZED + PAGINATION]
import os
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    Avg, Case, Count, F, FloatField, IntegerField, OuterRef, Prefetch, Q, Subquery, Sum, Value, When,
)
from django.db.models.functions import Coalesce
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST
from django.utils import timezone

from ..decorators import role_required
from ..forms import AssetForm, AssetUpdateForm, WriteOffForm, flash_form_errors
from ..models import (
    Asset, AssetAssignment, AssetComponent, AssetPhoto, AssetGroup, AssetCategory,
    Department, Location, MaintenanceRecord, MaintenanceSchedule,
    AssetWriteOff, BeritaAcara, DamageReport, cancel_write_off, compute_health, hapuskan_aset, life_review,
    initialize_schedule_for_asset,
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
        .select_related("lokasi_asset", "owned_by_department", "asset_category__group", "financial")
        .with_health_annotations()
        .annotate(
            component_count=Coalesce(component_count, 0),
            # Path foto utama (atau terbaru) untuk thumbnail di daftar
            photo_path=Subquery(
                AssetPhoto.objects.filter(asset=OuterRef('pk'))
                .order_by('-is_primary', '-uploaded_at').values('photo')[:1]
            ),
        )
    )

    if search_query:
        assets_qs = assets_qs.filter(
            Q(nama_asset__icontains=search_query)   |
            Q(asset_number__icontains=search_query) |
            Q(no_po__icontains=search_query)        |
            Q(asset_category__name__icontains=search_query) |
            Q(asset_category__group__name__icontains=search_query) |
            Q(lokasi_asset__name__icontains=search_query)   |
            Q(owned_by_department__name__icontains=search_query)
        )

    if category_filter.isdigit():
        assets_qs = assets_qs.filter(asset_category_id=category_filter)
    if group_filter.isdigit():
        assets_qs = assets_qs.filter(asset_category__group_id=group_filter)
    if location_filter.isdigit():
        assets_qs = assets_qs.filter(lokasi_asset_id=location_filter)
    elif location_filter == 'none':
        assets_qs = assets_qs.filter(lokasi_asset__isnull=True)

    if status_filter in ['active', 'maintenance', 'writeoff']:
        assets_qs = assets_qs.filter(lifecycle_status=status_filter)
    elif financial_filter == 'no':
        assets_qs = assets_qs.filter(financial__isnull=True)

    # Jumlah critical dihitung di SQL — tidak perlu load semua aset ke memori
    critical_qs = assets_qs.critical_health()
    if health_filter == 'critical':
        assets_qs = critical_qs

    # ── Urutan kolom (?sort=name / -name, dst.) ──
    sort = request.GET.get('sort', '')
    sort_key = sort.lstrip('-')
    if sort_key in SORT_FIELDS:
        if sort_key == 'health':
            assets_qs = assets_qs.annotate(ratio_annot=Case(
                When(financial__nilai_awal_aset__gt=0,
                     then=F('total_biaya_annot') * 100.0 / F('financial__nilai_awal_aset')),
                default=Value(None), output_field=FloatField(),
            ))
        expr = F(SORT_FIELDS[sort_key])
        desc = sort.startswith('-') != (sort_key in SORT_DESC_FIRST)
        assets_qs = assets_qs.order_by(expr.desc(nulls_last=True) if desc else expr.asc(nulls_last=True), 'id')
    else:
        sort = ''
        assets_qs = assets_qs.order_by('id')   # Paginator butuh urutan pasti

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
        "sort":                  sort,
        # Klik pertama mengurutkan, klik kedua membalik
        "sort_next":             {k: (f'-{k}' if sort == k else k) for k in SORT_FIELDS},
        "sort_dir":              _sort_dir(sort),
    }
    return render(request, "asset/list.html", context)


# Kunci ?sort= → kolom. Kunci di SORT_DESC_FIRST dibalik supaya klik pertama
# langsung menampilkan yang paling relevan (aset termuda, rasio biaya tertinggi).
SORT_FIELDS = {
    'name': 'nama_asset', 'category': 'asset_category__name', 'location': 'lokasi_asset__full_path',
    'department': 'owned_by_department__name', 'age': 'tanggal_pembelian', 'status': 'lifecycle_status',
    'health': 'ratio_annot', 'bom': 'component_count',
}
SORT_DESC_FIRST = {'age', 'health', 'bom'}


def _sort_dir(sort):
    """Arah urutan per kolom seperti yang dilihat user (untuk aria-sort & ikon panah)."""
    key = sort.lstrip('-')
    dirs = dict.fromkeys(SORT_FIELDS, 'none')
    if key in SORT_FIELDS:
        desc = sort.startswith('-') != (key in SORT_DESC_FIRST)
        if key == 'age':          # kolom menampilkan umur, kebalikan dari tanggal beli
            desc = not desc
        dirs[key] = 'descending' if desc else 'ascending'
    return dirs


@role_required(is_ga)
def asset_write_off(request, pk):
    """Write off resmi: catat tanggal, alasan, dan nilai buku; tutup jadwal, pemegang, dan laporan aktif."""
    asset = get_object_or_404(
        Asset.objects.select_related('financial', 'asset_category', 'lokasi_asset', 'owned_by_department')
        .with_health_annotations(), pk=pk)
    if asset.lifecycle_status == 'writeoff':
        messages.info(request, 'Aset ini sudah di-write off.')
        return redirect('asset_detail', pk=pk)

    form = WriteOffForm(request.POST or None, asset=asset)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        rujukan = BeritaAcara.objects.filter(jenis='kerusakan', status='ditandatangani', items__asset=asset,
                                             items__rekomendasi='hapus').order_by('-tanggal').first()
        ba = hapuskan_aset([asset], d['tanggal'], d['alasan'], d['keterangan'], by=request.user.employee,
                           rujukan=rujukan, nilai_jual=d['nilai_jual'])
        wo = AssetWriteOff.objects.get(asset=asset)
        messages.success(request, f"{asset.nama_asset} di-write off per {wo.tanggal:%d %b %Y}. {ba.nomor} terbit."
                         + (f" Nilai buku yang dihapus: Rp {wo.nilai_buku:,.0f}.".replace(',', '.') if wo.nilai_buku is not None else ""))
        return redirect('ba_detail', pk=ba.pk)

    fin = asset.financial if hasattr(asset, 'financial') else None
    health, ratio = compute_health(asset.total_biaya_annot, asset.jumlah_corrective, fin.nilai_awal_aset if fin else None)
    assignment = AssetAssignment.objects.filter(asset=asset, status='active').select_related('assigned_to', 'department').first()
    # Alasan yang disarankan dari kondisi aset (tetap bisa diganti)
    suggested = 'rusak' if health in ('critical', 'waspada') or asset.lifecycle_status == 'maintenance' else \
                'usang' if fin and fin.sisa_umur_ekonomis() <= 0 else ''
    return render(request, 'asset/write_off.html', {
        'asset':          asset,
        'form':           form,
        'fin':            fin,
        'book_today':     fin.book_value() if fin else None,
        'health':         health,
        'ratio':          ratio,
        'assignment':     assignment,
        'schedule_count': asset.schedules.active().count(),
        'report_count':   DamageReport.objects.filter(
                              asset=asset, report_status__in=DamageReport.ACTIVE_STATUS).count(),
        'reasons':        AssetWriteOff.REASON,
        'cur_reason':     form.data.get('alasan') or suggested,
        'today':          timezone.localdate(),
    })


@role_required(is_ga)
@require_POST
def asset_write_off_cancel(request, pk):
    asset = get_object_or_404(Asset.objects.select_related('write_off'), pk=pk, lifecycle_status='writeoff')
    cancel_write_off(asset, by=request.user.employee)
    messages.success(request, f"Write off {asset.nama_asset} dibatalkan. Aset aktif kembali dan jadwal maintenance dibuat ulang.")
    return redirect('asset_detail', pk=pk)


@role_required(is_ga_or_finance)
def asset_quickview(request, pk):
    """Ringkasan satu aset untuk panel samping di daftar aset (HTML parsial)."""
    asset = get_object_or_404(
        Asset.objects.select_related(
            'lokasi_asset', 'owned_by_department', 'asset_category__group', 'financial', 'maintenance_config',
        ).with_health_annotations(),
        pk=pk,
    )
    nilai_awal = asset.financial.nilai_awal_aset if hasattr(asset, 'financial') else None
    health, ratio = compute_health(asset.total_biaya_annot, asset.jumlah_corrective, nilai_awal)
    assignment = AssetAssignment.objects.filter(asset=asset, status='active').select_related('assigned_to', 'department').first()
    context = {
        'asset':         asset,
        'photo':         asset.photos.order_by('-is_primary', '-uploaded_at').first(),
        'photo_count':   asset.photos.count(),
        'health':        health,
        'ratio':         ratio,
        'book_value':    asset.financial.book_value() if nilai_awal is not None else None,
        'next_schedule': asset.schedules.active().order_by('scheduled_date').first(),
        'last_record':   asset.maintenance_records.select_related('performed_by')
                              .order_by('-tanggal_pelaksanaan', '-id').first(),
        'open_report':   asset.get_pending_damage_report(),
        'assignment':    assignment,
        'interval':      asset.get_effective_interval_days(),
    }
    return render(request, 'asset/_quickview.html', context)


@role_required(is_ga_or_finance)
def asset_search(request):
    """Pencarian ringan untuk palet perintah (Ctrl+K). Maksimal 8 hasil."""
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'results': [], 'total': 0})
    qs = Asset.objects.all()
    # Setiap kata harus cocok di salah satu kolom: "ac mess 3" → AC di Mess 3
    for term in q.split()[:5]:
        qs = qs.filter(Q(nama_asset__icontains=term) | Q(asset_number__icontains=term) | Q(no_po__icontains=term) |
                       Q(lokasi_asset__name__icontains=term) | Q(asset_category__name__icontains=term))
    qs = (qs
          .select_related('lokasi_asset', 'asset_category')
          .annotate(photo_path=Subquery(
              AssetPhoto.objects.filter(asset=OuterRef('pk')).order_by('-is_primary', '-uploaded_at').values('photo')[:1]))
          .order_by('nama_asset', 'id'))
    can_log = is_ga(request.user)
    status_label = {'active': 'Aktif', 'maintenance': 'Maintenance', 'writeoff': 'Write off'}
    results = [{
        'id':           a.pk,
        'name':         a.nama_asset,
        'number':       a.asset_number,
        'meta':         ' · '.join(x for x in [a.asset_category.name if a.asset_category else '',
                                               str(a.lokasi_asset) if a.lokasi_asset else 'Belum ditempatkan'] if x),
        'status':       a.lifecycle_status,
        'status_label': status_label.get(a.lifecycle_status, ''),
        'photo':        f'{settings.MEDIA_URL}{a.photo_path}' if a.photo_path else '',
        'url':          reverse('asset_detail', args=[a.pk]),
        'maint_url':    reverse('maintenance_create', args=[a.pk]) if can_log and a.lifecycle_status != 'writeoff' else '',
    } for a in qs[:8]]
    return JsonResponse({'results': results, 'total': qs.count(),
                         'all_url': f"{reverse('asset_list')}?{urlencode({'q': q})}"})


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
            "lokasi_asset", "owned_by_department",
            "asset_category__group", "financial", "maintenance_config",
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
    # Kesehatan dinilai dari biaya perbaikan (corrective) saja, bukan total maintenance
    health = asset.health_report(total_biaya=stats['total_biaya_corrective'] or Decimal('0'), breakdown=corrective_count)

    def pct(n):
        return round((n or 0) / corrective_count * 100) if corrective_count else 0

    # Posisi nilai buku di antara nilai residual (0%) dan nilai awal (100%)
    book_value = book_value_pct = life = None
    if hasattr(asset, 'financial'):
        fin = asset.financial
        if asset.lifecycle_status != 'writeoff':
            year_ago = timezone.localdate() - timedelta(days=365)
            recent = sum(1 for r in all_records if r.maintenance_type == 'corrective' and r.tanggal_pelaksanaan > year_ago)
            life = life_review(fin, stats['total_biaya_corrective'] or Decimal('0'), corrective_count, recent)
        book_value = fin.book_value()
        rentang = fin.nilai_awal_aset - fin.nilai_residual
        book_value_pct = round(float((book_value - fin.nilai_residual) / rentang * 100)) if rentang > 0 else 0

    context = {
        "book_value":           book_value,
        "book_value_pct":       book_value_pct,
        "wo":                   getattr(asset, 'write_off', None) if asset.lifecycle_status == 'writeoff' else None,
        "asset":                asset,
        "assignment":           AssetAssignment.objects.filter(asset=asset, status='active')
                                .select_related('assigned_to', 'department').first(),
        "maintenance_records":  all_records,
        "corrective_records":   [r for r in all_records if r.maintenance_type == 'corrective'],
        "schedules":            schedules,
        "components":           asset.components.all(),
        "photos":               photos,
        "primary_photo":        next((p for p in photos if p.is_primary), None),
        # Jadwal aktif terdekat — termasuk yang sudah terlambat
        "next_schedule":        next((s for s in schedules if s.status in ('upcoming', 'overdue')), None),
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
        "health_cause":           health['cause'],
        "life":                   life,
    }
    return render(request, "asset/detail.html", context)


@role_required(is_ga)
@require_POST
def asset_delete(request, pk):
    # POST saja: link GET bisa terpicu prefetch browser atau tautan yang dibagikan
    asset = get_object_or_404(Asset, pk=pk)
    name = asset.nama_asset
    back_params = request.GET.urlencode()  # otomatis berisi page, q, status, dll
    asset.delete()
    messages.success(request, f"Aset {name} dihapus permanen.")
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