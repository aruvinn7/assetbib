# views/dashboard.py  [v2 — with chart data]
from datetime import date, timedelta
from django.db.models import Count, Prefetch, Q, Sum
from django.db.models.functions import TruncMonth
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from ..decorators import role_required
from ..roles import is_ga
from ..models import (
    Asset, AssetFinancial, MaintenanceRecord,
    MaintenanceSchedule, DamageReport,
    AssetGroup, AssetCategory, Location,
    attach_maintenance_status, refresh_overdue_schedules_daily,
)


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: bangun location tree + propagasi count (dipakai oleh dua view)
# ─────────────────────────────────────────────────────────────────────────────
def _build_location_tree():
    all_locations = list(
        Location.objects.annotate(
            direct_total    = Count('asset'),
            direct_active   = Count('asset', filter=Q(asset__lifecycle_status='active')),
            direct_maint    = Count('asset', filter=Q(asset__lifecycle_status='maintenance')),
            direct_writeoff = Count('asset', filter=Q(asset__lifecycle_status='writeoff')),
        ).order_by('name')
    )

    loc_map = {loc.pk: loc for loc in all_locations}
    for loc in all_locations:
        loc.total_assets   = loc.direct_total
        loc.total_active   = loc.direct_active
        loc.total_maint    = loc.direct_maint
        loc.total_writeoff = loc.direct_writeoff
        loc.children_list  = []

    roots = []
    for loc in all_locations:
        if loc.parent_id and loc.parent_id in loc_map:
            loc_map[loc.parent_id].children_list.append(loc)
        elif not loc.parent_id:
            roots.append(loc)

    def _propagate(node):
        for child in node.children_list:
            _propagate(child)
            node.total_assets   += child.total_assets
            node.total_active   += child.total_active
            node.total_maint    += child.total_maint
            node.total_writeoff += child.total_writeoff

    for root in roots:
        _propagate(root)

    return roots, loc_map


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: chart data — maintenance trend & new assets (6 bulan terakhir)
# ─────────────────────────────────────────────────────────────────────────────
def _build_chart_data():
    """
    Hitung data 6 bulan terakhir untuk 2 chart:
      1. Maintenance records per bulan (preventive vs corrective)
      2. Aset baru ditambah per bulan

    Menggunakan TruncMonth + annotate — 2 query total.
    Return: dict list/angka biasa — dikirim ke JS lewat |json_script di template.
    """
    today      = date.today()
    # Awal bulan, 5 bulan sebelum bulan ini → 6 bulan termasuk bulan berjalan
    six_months_ago = today.replace(day=1)
    for _ in range(5):
        six_months_ago = (six_months_ago - timedelta(days=1)).replace(day=1)

    # ── Maintenance records per bulan ──
    maint_qs = (
        MaintenanceRecord.objects
        .filter(tanggal_pelaksanaan__gte=six_months_ago)
        .annotate(month=TruncMonth('tanggal_pelaksanaan'))
        .values('month', 'maintenance_type')
        .annotate(count=Count('id'))
        .order_by('month')
    )

    # ── Aset baru per bulan ──
    asset_qs = (
        Asset.objects
        .filter(created_at__gte=six_months_ago)
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )

    # Build bulan labels (6 bulan terakhir termasuk bulan ini)
    MONTHS_ID = ['Jan','Feb','Mar','Apr','Mei','Jun','Jul','Agu','Sep','Okt','Nov','Des']
    months = []
    cur = six_months_ago
    for _ in range(6):
        months.append(cur)
        # next month
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)

    labels = [f"{MONTHS_ID[m.month - 1]} {str(m.year)[2:]}" for m in months]

    # ── Biaya maintenance per bulan per tipe ──
    cost_qs = (
        MaintenanceRecord.objects
        .filter(tanggal_pelaksanaan__gte=six_months_ago)
        .annotate(month=TruncMonth('tanggal_pelaksanaan'))
        .values('month', 'maintenance_type')
        .annotate(total_cost=Sum('biaya'))
        .order_by('month')
    )

    # Aggregate maintenance (count)
    maint_map = {}  # {(month, type): count}
    for row in maint_qs:
        key = (row['month'].replace(day=1), row['maintenance_type'])
        maint_map[key] = row['count']

    preventive = [maint_map.get((m, 'preventive'), 0) for m in months]
    corrective  = [maint_map.get((m, 'corrective'), 0) for m in months]

    # Aggregate new assets
    # created_at adalah DateTimeField → TruncMonth mengembalikan datetime, samakan ke date
    asset_map = {timezone.localtime(row['month']).date().replace(day=1): row['count'] for row in asset_qs}
    new_assets = [asset_map.get(m, 0) for m in months]

    # Aggregate biaya per tipe
    cost_map = {}  # {(month, type): total_cost float}
    for row in cost_qs:
        key = (row['month'].replace(day=1), row['maintenance_type'])
        cost_map[key] = float(row['total_cost'] or 0)

    cost_prev = [cost_map.get((m, 'preventive'), 0) for m in months]
    cost_corr = [cost_map.get((m, 'corrective'), 0) for m in months]
    cost_insp = [cost_map.get((m, 'inspection'), 0) for m in months]

    return {
        'labels':     labels,
        'maint':      {'preventive': preventive, 'corrective': corrective},
        'new_assets': new_assets,
        'cost':       {
            'preventive': cost_prev,
            'corrective': cost_corr,
            'inspection': cost_insp,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# VIEW: Dashboard Utama
# ─────────────────────────────────────────────────────────────────────────────
@login_required(login_url='login')
def dashboard(request):
    refresh_overdue_schedules_daily()

    today            = date.today()
    seven_days_later = today + timedelta(days=7)

    # 1 query — semua stat Asset (termasuk location stats)
    asset_stats = Asset.objects.aggregate(
        total          = Count('id'),
        active         = Count('id', filter=Q(lifecycle_status='active')),
        in_maintenance = Count('id', filter=Q(lifecycle_status='maintenance')),
        writeoff       = Count('id', filter=Q(lifecycle_status='writeoff')),
        unvalued       = Count('id', filter=Q(financial__isnull=True)),
        with_location  = Count('id', filter=Q(lokasi_asset__isnull=False)),
        no_location    = Count('id', filter=Q(lokasi_asset__isnull=True)),
    )

    # 1 query — MaintenanceSchedule stats
    schedule_stats = MaintenanceSchedule.objects.aggregate(
        overdue       = Count('id', filter=Q(status='overdue')),
        upcoming_soon = Count('id', filter=Q(
            status='upcoming',
            scheduled_date__range=[today, seven_days_later]
        )),
    )

    # 1 query — DamageReport stats
    damage_stats = DamageReport.objects.aggregate(
        pending  = Count('id', filter=Q(report_status='pending')),
        critical = Count('id', filter=Q(
            severity='critical',
            report_status__in=['pending', 'acknowledged', 'in_progress']
        )),
    )

    # 2 queries — asset group breakdown
    asset_groups = (
        AssetGroup.objects
        .prefetch_related(
            Prefetch(
                'categories',
                queryset=AssetCategory.objects.annotate(
                    total       = Count('assets'),
                    active      = Count('assets', filter=Q(assets__lifecycle_status='active')),
                    maintenance = Count('assets', filter=Q(assets__lifecycle_status='maintenance')),
                    writeoff    = Count('assets', filter=Q(assets__lifecycle_status='writeoff')),
                ).order_by('name'),
                to_attr='categories_with_stats',
            )
        )
        .annotate(
            total       = Count('categories__assets'),
            active      = Count('categories__assets', filter=Q(categories__assets__lifecycle_status='active')),
            maintenance = Count('categories__assets', filter=Q(categories__assets__lifecycle_status='maintenance')),
            writeoff    = Count('categories__assets', filter=Q(categories__assets__lifecycle_status='writeoff')),
        )
        .filter(total__gt=0)
        .order_by('name')
    )

    # 1 query — location tree
    roots, _ = _build_location_tree()

    # 2 queries — chart data
    chart = _build_chart_data()

    context = {
        # ── Asset stats ──
        'total_asset':               asset_stats['total'],
        'total_financial':           AssetFinancial.objects.count(),
        'active_count':              asset_stats['active'],
        'maintenance_status_count':  asset_stats['in_maintenance'],
        'writeoff_count':            asset_stats['writeoff'],
        'unvalued_count':            asset_stats['unvalued'],
        # Untuk donut komposisi status: aktif, maintenance, write off
        'status_counts':             [asset_stats['active'], asset_stats['in_maintenance'], asset_stats['writeoff']],
        'critical_health_count':     Asset.objects.with_health_annotations().critical_health().count(),

        # ── Maintenance stats ──
        'overdue_count':             schedule_stats['overdue'],
        'upcoming_soon_count':       schedule_stats['upcoming_soon'],

        # ── Damage stats ──
        'pending_damage_count':      damage_stats['pending'],
        'critical_damage_count':     damage_stats['critical'],

        # ── Groups ──
        'asset_groups':              asset_groups,

        # ── Location widget ──
        'roots':                     roots,
        'total_with_location':       asset_stats['with_location'],
        'total_without_location':    asset_stats['no_location'],

        # ── Chart data (JSON strings, safe untuk |safe filter) ──
        'maint_trend_labels':        chart['labels'],
        'maint_trend_data':          chart['maint'],
        'new_asset_labels':          chart['labels'],
        'new_asset_data':            chart['new_assets'],
        'maint_cost_data':           chart['cost'],

        # ── Table data (minimal columns) ──
        'critical_schedules': (
            MaintenanceSchedule.objects
            .filter(status__in=['upcoming', 'overdue'])
            .select_related('asset', 'asset__asset_category', 'asset__lokasi_asset')
            .only(
                'id', 'status', 'scheduled_date',
                'asset__id', 'asset__nama_asset', 'asset__asset_number',
                'asset__lokasi_asset__name', 'asset__lokasi_asset__full_path',
                'asset__asset_category__name',
            )
            .order_by('scheduled_date')[:10]
        ),

        # maintenance_status dihitung massal (1 query), bukan 2 query per aset
        'recent_assets': attach_maintenance_status(
            Asset.objects
            .select_related('asset_category', 'asset_group', 'lokasi_asset', 'maintenance_config')
            .order_by('-id')[:10]
        ),

        'pending_damage_reports': (
            DamageReport.objects
            .filter(report_status__in=['pending', 'acknowledged', 'in_progress'])
            .select_related('asset', 'reported_by')
            .only(
                'id', 'title', 'severity', 'report_status', 'reported_at',
                'asset__id', 'asset__nama_asset', 'asset__asset_number',
                'reported_by__nama',
            )
            .order_by('-reported_at')[:5]
        ),
    }
    return render(request, 'dashboard.html', context)


# ─────────────────────────────────────────────────────────────────────────────
# VIEW: Dashboard Lokasi (detail)
# ─────────────────────────────────────────────────────────────────────────────
@role_required(is_ga)
def location_dashboard(request):
    selected_id = request.GET.get('location', '')

    roots, loc_map = _build_location_tree()

    selected_node = None
    breadcrumb    = []

    if selected_id and selected_id.isdigit():
        selected_node = loc_map.get(int(selected_id))
        if selected_node:
            crumb_node  = selected_node
            crumb_chain = []
            while crumb_node:
                crumb_chain.append(crumb_node)
                crumb_node = loc_map.get(crumb_node.parent_id) if crumb_node.parent_id else None
            breadcrumb = list(reversed(crumb_chain))

    selected_assets = []
    if selected_node:
        def _collect_pks(node):
            pks = [node.pk]
            for child in node.children_list:
                pks.extend(_collect_pks(child))
            return pks

        all_pks = _collect_pks(selected_node)
        selected_assets = list(
            Asset.objects
            .filter(lokasi_asset_id__in=all_pks)
            .select_related(
                'asset_group', 'asset_category',
                'owned_by_department', 'lokasi_asset',
            )
            .order_by('lifecycle_status', 'nama_asset')
        )

    loc_stats = Asset.objects.aggregate(
        with_loc    = Count('id', filter=Q(lokasi_asset__isnull=False)),
        without_loc = Count('id', filter=Q(lokasi_asset__isnull=True)),
    )

    context = {
        'roots':                  roots,
        'loc_map':                loc_map,
        'selected_node':          selected_node,
        'selected_assets':        selected_assets,
        'breadcrumb':             breadcrumb,
        'total_with_location':    loc_stats['with_loc'],
        'total_without_location': loc_stats['without_loc'],
        'selected_id':            selected_id,
    }
    return render(request, 'dashboard/location.html', context)