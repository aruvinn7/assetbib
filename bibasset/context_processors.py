from django.utils.functional import SimpleLazyObject

from .roles import get_user_role


def user_roles(request):
    """
    Satu sumber role untuk template: profile Employee (sama dengan roles.py
    yang dipakai decorator), bukan auth Group. Lazy — query hanya jalan
    kalau template benar-benar memakai variabel ini.
    """
    role = SimpleLazyObject(lambda: get_user_role(request.user) or '')
    return {
        'user_role':    role,
        'is_ga':        SimpleLazyObject(lambda: role == 'ga'),
        'is_finance':   SimpleLazyObject(lambda: role == 'finance'),
        'is_user_role': SimpleLazyObject(lambda: role == 'user'),
        # Lazy: dihitung hanya saat lonceng di header dirender
        'notifications': SimpleLazyObject(lambda: _build_notifications(role)),
    }


def _build_notifications(role):
    """Pengingat untuk lonceng header (GA & Finance), dibangun dari hitungan ringan."""
    from datetime import date, timedelta

    from django.db.models import Count, Q
    from django.urls import reverse

    from django.db.models import Min

    from .models import Asset, BeritaAcara, BeritaAcaraItem, DamageReport, MaintenanceSchedule

    items = []
    if role == 'ga':
        today = date.today()
        sched = MaintenanceSchedule.objects.aggregate(
            overdue=Count('id', filter=Q(status='overdue')),
            soon=Count('id', filter=Q(status='upcoming', scheduled_date__range=[today, today + timedelta(days=7)])),
        )
        damage = DamageReport.objects.aggregate(
            pending=Count('id', filter=Q(report_status='pending')),
            critical=Count('id', filter=Q(severity='critical', report_status__in=['pending', 'acknowledged', 'in_progress'])),
        )
        no_location = Asset.objects.filter(lokasi_asset__isnull=True).count()
        maint_url = reverse('maintenance_list')
        if sched['overdue']:
            items.append({'kind': 'warning', 'text': f"{sched['overdue']} jadwal maintenance terlambat",
                          'sub': 'Perlu dikerjakan segera', 'url': f'{maint_url}?status=overdue'})
        if damage['pending']:
            sub = f"{damage['critical']} di antaranya kritis" if damage['critical'] else 'Menunggu tindak lanjut GA'
            items.append({'kind': 'report', 'text': f"{damage['pending']} laporan kerusakan baru",
                          'sub': sub, 'url': reverse('ga_damage_report_list')})
        if sched['soon']:
            items.append({'kind': 'mandatory', 'text': f"{sched['soon']} maintenance jatuh tempo ≤ 7 hari",
                          'sub': 'Siapkan jadwal pengerjaan', 'url': f'{maint_url}?status=upcoming'})
        if no_location:
            items.append({'kind': 'none', 'text': f'{no_location} aset belum punya lokasi',
                          'sub': 'Tempatkan agar mudah dilacak', 'url': reverse('asset_list') + '?location=none'})
        # Berita acara: temuan yang menumpuk ≥ 7 hari di draf, dan BA terbit yang belum ditandatangani
        draf = BeritaAcaraItem.objects.filter(ba__status='draft', ba__jenis='kerusakan').aggregate(n=Count('id'), awal=Min('created_at'))
        if draf['n'] and (date.today() - draf['awal'].date()).days >= 7:
            items.append({'kind': 'report', 'text': f"{draf['n']} temuan kerusakan belum dibuatkan berita acara",
                          'sub': 'Tinjau draf lalu terbitkan', 'url': reverse('ba_list')})
        ttd = BeritaAcara.objects.filter(status='terbit').count()
        if ttd:
            items.append({'kind': 'mandatory', 'text': f'{ttd} berita acara menunggu tanda tangan',
                          'sub': 'Cetak lalu tandai sudah ditandatangani', 'url': reverse('ba_list') + '?tab=kerusakan'})
    elif role == 'finance':
        unvalued = Asset.objects.filter(financial__isnull=True).count()
        if unvalued:
            items.append({'kind': 'none', 'text': f'{unvalued} aset belum dinilai',
                          'sub': 'Isi data finansial', 'url': reverse('asset_list') + '?financial=no'})
    return items
