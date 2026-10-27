from django.utils.functional import SimpleLazyObject
from django.utils import timezone

from django.urls import reverse

from .roles import get_user_role, home_url_name


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
        'home_url':     SimpleLazyObject(lambda: reverse(home_url_name(request.user) or 'login')),
        'pending_movement_reports': SimpleLazyObject(lambda: _pending_movement_reports(role)),
        # Lazy: dihitung hanya saat lonceng di header dirender
        'notifications': SimpleLazyObject(lambda: _build_notifications(role)),
    }


def _pending_movement_reports(role):
    if role != 'ga':
        return 0
    from .models import MovementReport
    return MovementReport.objects.filter(status='pending').count()


def _build_notifications(role):
    """Pengingat untuk lonceng header (GA & Finance), dibangun dari hitungan ringan."""
    from datetime import timedelta

    from django.db.models import Count, Q
    from django.urls import reverse

    from django.db.models import Min

    from .models import Asset, BeritaAcara, BeritaAcaraItem, DamageReport, MaintenanceSchedule

    items = []
    if role == 'ga':
        today = timezone.localdate()
        sched = MaintenanceSchedule.objects.aggregate(
            overdue=Count('id', filter=Q(status='overdue')),
            soon=Count('id', filter=Q(status='upcoming', scheduled_date__range=[today, today + timedelta(days=7)])),
        )
        damage = DamageReport.objects.aggregate(
            pending=Count('id', filter=Q(report_status='pending')),
            critical=Count('id', filter=Q(severity='critical', report_status__in=DamageReport.ACTIVE_STATUS)),
        )
        no_location = Asset.objects.filter(lokasi_asset__isnull=True).count()
        moves = _pending_movement_reports(role)
        if moves:
            items.append({'kind': 'mandatory', 'text': f"{moves} laporan pindah aset menunggu persetujuan",
                          'sub': 'Pindah departemen atau pindah site', 'url': reverse('ga_movement_report_list')})
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
        if draf['n'] and (timezone.localdate() - draf['awal'].date()).days >= 7:
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
