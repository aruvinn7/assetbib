# views/analytics.py
# Dua dashboard analitik:
#   - Nilai aset      → Finance (dan GA): nilai perolehan, nilai buku, penyusutan, umur ekonomis
#   - Kinerja maint.  → GA (dan Finance): kepatuhan jadwal, biaya, laporan kerusakan, kandidat writeoff
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import render
from django.utils import timezone

from ..decorators import role_required
from ..models import (
    Asset, AssetFinancial, AssetWriteOff, DamageReport, MaintenanceRecord, MaintenanceSchedule, compute_health,
)
from .utils import is_ga_or_finance

MONTHS_ID = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des']


def _book_value_at(fin, on):
    """Nilai buku pada tanggal `on` — rumus sama dengan AssetFinancial.book_value()."""
    days = (on - fin.asset.tanggal_pembelian).days
    if days <= 0:
        return fin.nilai_awal_aset
    years = (Decimal(days) / Decimal('365.25')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
    years = min(years, Decimal(fin.umur_ekonomis))
    return max(fin.nilai_awal_aset - fin.depresiasi_tahunan() * years, fin.nilai_residual)


def _month_starts(n, today):
    """Awal bulan untuk n bulan terakhir, termasuk bulan berjalan (urut lama → baru)."""
    first = today.replace(day=1)
    months = [first]
    for _ in range(n - 1):
        first = (first - timedelta(days=1)).replace(day=1)
        months.insert(0, first)
    return months


# ─────────────────────────────────────────────────────────────────────────────
# Nilai aset & penyusutan
# ─────────────────────────────────────────────────────────────────────────────
@role_required(is_ga_or_finance)
def value_dashboard(request):
    today = timezone.localdate()
    jan1, dec31 = date(today.year, 1, 1), date(today.year, 12, 31)

    fins = (AssetFinancial.objects
            .select_related('asset', 'asset__asset_category__group', 'asset__owned_by_department')
            .exclude(asset__lifecycle_status='writeoff'))

    tot = defaultdict(Decimal)
    by_group = defaultdict(lambda: {'cost': Decimal(0), 'book': Decimal(0), 'count': 0})
    by_dept = defaultdict(lambda: {'cost': Decimal(0), 'book': Decimal(0), 'count': 0})
    by_year = defaultdict(Decimal)
    expired, expiring, top_book = [], [], []

    for f in fins:
        a = f.asset
        book = f.book_value()
        start = _book_value_at(f, max(jan1, a.tanggal_pembelian))
        tot['cost'] += f.nilai_awal_aset
        tot['book'] += book
        tot['dep_ytd'] += start - book
        tot['dep_full_year'] += start - _book_value_at(f, dec31)
        tot['count'] += 1

        g = by_group[a.asset_category.group.name if a.asset_category else 'Tanpa grup']
        g['cost'] += f.nilai_awal_aset; g['book'] += book; g['count'] += 1
        d = by_dept[a.owned_by_department.name if a.owned_by_department else 'Tanpa departemen']
        d['cost'] += f.nilai_awal_aset; d['book'] += book; d['count'] += 1
        by_year[a.tanggal_pembelian.year] += f.nilai_awal_aset

        sisa = f.sisa_umur_ekonomis()
        row = {'asset': a, 'cost': f.nilai_awal_aset, 'book': book, 'sisa': sisa, 'umur': f.umur_ekonomis,
               'end': a.tanggal_pembelian + timedelta(days=round(365.25 * f.umur_ekonomis))}
        if sisa <= 0:
            expired.append(row)
        elif sisa <= 1:
            expiring.append(row)
        top_book.append(row)

    tot['accum'] = tot['cost'] - tot['book']
    groups = sorted(({'name': k, **v} for k, v in by_group.items()), key=lambda x: -x['cost'])
    depts = sorted(({'name': k, **v} for k, v in by_dept.items()), key=lambda x: -x['book'])
    years = sorted(by_year)

    write_offs = list(AssetWriteOff.objects.select_related('asset', 'created_by').order_by('-tanggal', '-id'))
    wo_year = [w for w in write_offs if w.tanggal.year == today.year]
    context = {
        'today':          today,
        'tot':            tot,
        'accum_pct':      round(tot['accum'] / tot['cost'] * 100) if tot['cost'] else 0,
        'groups':         groups,
        'depts':          depts[:10],
        'dept_more':      max(len(depts) - 10, 0),
        'expired':        sorted(expired, key=lambda r: -r['cost'])[:10],
        'expired_count':  len(expired),
        'expired_cost':   sum((r['cost'] for r in expired), Decimal(0)),
        'expiring':       sorted(expiring, key=lambda r: r['sisa'])[:10],
        'expiring_count': len(expiring),
        'top_book':       sorted(top_book, key=lambda r: -r['book'])[:8],
        'unvalued_count': Asset.objects.filter(financial__isnull=True).count(),
        'write_offs':     write_offs[:8],
        'writeoff_count': len(write_offs),
        'wo_year_count':  len(wo_year),
        'wo_year_book':   sum((w.nilai_buku or 0 for w in wo_year), Decimal(0)),
        'wo_year_gain':   sum((w.gain_loss() or 0 for w in wo_year), Decimal(0)),
        # Data grafik
        'chart_groups':   {'labels': [g['name'] for g in groups],
                           'book': [float(g['book']) for g in groups],
                           'accum': [float(g['cost'] - g['book']) for g in groups]},
        'chart_years':    {'labels': [str(y) for y in years], 'cost': [float(by_year[y]) for y in years]},
    }
    return render(request, 'dashboard/value.html', context)


# ─────────────────────────────────────────────────────────────────────────────
# Kinerja maintenance & kandidat writeoff
# ─────────────────────────────────────────────────────────────────────────────
ON_TIME_GRACE = 7  # hari toleransi: dikerjakan ≤ 7 hari setelah jatuh tempo masih dianggap tepat waktu


@role_required(is_ga_or_finance)
def performance_dashboard(request):
    today = timezone.localdate()
    months = _month_starts(12, today)
    start = months[0]
    prev_start = date(start.year - 1, start.month, 1)

    recs = MaintenanceRecord.objects.filter(tanggal_pelaksanaan__gte=start, tanggal_pelaksanaan__lte=today)

    # ── Biaya per bulan: perbaikan vs perawatan rutin ──
    cost_map = defaultdict(Decimal)
    for row in (recs.annotate(m=TruncMonth('tanggal_pelaksanaan'))
                .values('m', 'maintenance_type').annotate(s=Sum('biaya'))):
        kind = 'corrective' if row['maintenance_type'] == 'corrective' else 'routine'
        cost_map[(row['m'], kind)] += row['s'] or 0
    cost_routine = [float(cost_map[(m, 'routine')]) for m in months]
    cost_corr = [float(cost_map[(m, 'corrective')]) for m in months]
    cost_12 = sum(cost_routine) + sum(cost_corr)
    cost_prev = MaintenanceRecord.objects.filter(
        tanggal_pelaksanaan__gte=prev_start, tanggal_pelaksanaan__lt=start).aggregate(s=Sum('biaya'))['s'] or 0
    cost_delta = round((cost_12 - float(cost_prev)) / float(cost_prev) * 100) if cost_prev else None

    # ── Kepatuhan jadwal rutin (bukan dari laporan kerusakan) ──
    done = list(MaintenanceSchedule.objects
                .filter(status='done', damage_report__isnull=True, scheduled_date__gte=start,
                        scheduled_date__lte=today, maintenance_record__isnull=False)
                .values_list('scheduled_date', 'maintenance_record__tanggal_pelaksanaan'))
    on_time = sum(1 for due, did in done if did <= due + timedelta(days=ON_TIME_GRACE))
    overdue_now = MaintenanceSchedule.objects.filter(status='overdue', damage_report__isnull=True).count()
    # Jadwal yang lewat tempo dan belum dikerjakan juga dihitung sebagai tidak tepat waktu
    total_due = len(done) + overdue_now
    compliance = round(on_time / total_due * 100) if total_due else None

    # Kepatuhan per bulan jatuh tempo (untuk grafik)
    per_month = defaultdict(lambda: [0, 0])
    for due, did in done:
        k = due.replace(day=1)
        per_month[k][1] += 1
        if did <= due + timedelta(days=ON_TIME_GRACE):
            per_month[k][0] += 1
    compliance_series = [round(per_month[m][0] / per_month[m][1] * 100) if per_month[m][1] else None for m in months]

    # ── Laporan kerusakan ──
    resolved = (DamageReport.objects
                .filter(report_status='resolved', resolved_at__date__gte=start)
                .annotate(dur=ExpressionWrapper(F('resolved_at') - F('reported_at'), output_field=DurationField())))
    avg_dur = resolved.aggregate(a=Avg('dur'))['a']
    report_stats = DamageReport.objects.aggregate(
        active=Count('id', filter=Q(report_status__in=DamageReport.ACTIVE_STATUS)),
        pending=Count('id', filter=Q(report_status='pending')),
        period=Count('id', filter=Q(reported_at__date__gte=start)),
        rejected=Count('id', filter=Q(report_status='rejected', reported_at__date__gte=start)),
    )

    # ── Biaya per kategori (12 bulan) ──
    by_cat = list(recs.values('asset__asset_category__name')
                  .annotate(s=Sum('biaya'), corr=Count('id', filter=Q(maintenance_type='corrective')))
                  .filter(s__gt=0).order_by('-s'))
    top_cats = by_cat[:8]
    other = sum((r['s'] for r in by_cat[8:]), Decimal(0))
    chart_cats = {'labels': [r['asset__asset_category__name'] or 'Tanpa kategori' for r in top_cats],
                  'cost': [float(r['s']) for r in top_cats]}
    if other:
        chart_cats['labels'].append(f'Lainnya ({len(by_cat) - 8} kategori)')
        chart_cats['cost'].append(float(other))

    # ── Kandidat tinjau writeoff / penggantian ──
    candidates = []
    assets = (Asset.objects.exclude(lifecycle_status='writeoff')
              .select_related('financial', 'asset_category', 'lokasi_asset')
              .with_health_annotations())
    for a in assets:
        fin = getattr(a, 'financial', None)
        status, ratio = compute_health(a.total_biaya_annot, a.jumlah_corrective, fin.nilai_awal_aset if fin else None)
        reasons = []
        if status in ('critical', 'waspada'):
            reasons.append(f'Biaya perbaikan {ratio:.0f}% dari nilai awal')
        expired = fin is not None and fin.sisa_umur_ekonomis() <= 0
        if expired and a.jumlah_corrective >= 2:
            reasons.append('Umur ekonomis habis & sering rusak')
        if a.jumlah_corrective >= 4:
            reasons.append(f'{a.jumlah_corrective}× kerusakan')
        if reasons:
            candidates.append({'asset': a, 'status': status, 'ratio': ratio, 'reasons': reasons,
                               'book': fin.book_value() if fin else None, 'corrective': a.jumlah_corrective})
    candidates.sort(key=lambda c: (-(c['ratio'] or 0), -c['corrective']))

    # ── Aset paling sering rusak (12 bulan) ──
    frequent = list(recs.filter(maintenance_type='corrective')
                    .values('asset_id', 'asset__nama_asset', 'asset__asset_number')
                    .annotate(n=Count('id'), s=Sum('biaya')).filter(n__gte=2).order_by('-n', '-s')[:6])

    context = {
        'period_label':     f"{MONTHS_ID[start.month - 1]} {start.year} – {MONTHS_ID[today.month - 1]} {today.year}",
        'cost_12':          cost_12,
        'cost_corr_12':     sum(cost_corr),
        'cost_delta':       cost_delta,
        'compliance':       compliance,
        'on_time':          on_time,
        'total_due':        total_due,
        'overdue_now':      overdue_now,
        'grace':            ON_TIME_GRACE,
        # SQLite mengembalikan rata-rata durasi sebagai mikrodetik, bukan timedelta
        'avg_days':         None if avg_dur is None else round(
                                (avg_dur.total_seconds() if isinstance(avg_dur, timedelta) else avg_dur / 1e6) / 86400, 1),
        'resolved_count':   resolved.count(),
        'report_stats':     report_stats,
        'candidates':       candidates[:12],
        'candidate_count':  len(candidates),
        'frequent':         frequent,
        'chart_cost':       {'labels': [f"{MONTHS_ID[m.month - 1]} {str(m.year)[2:]}" for m in months],
                             'routine': cost_routine, 'corrective': cost_corr},
        'chart_compliance': compliance_series,
        'chart_cats':       chart_cats,
    }
    return render(request, 'dashboard/performance.html', context)
