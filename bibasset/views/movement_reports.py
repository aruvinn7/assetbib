# views/movement_reports.py
# Laporan perpindahan aset dari karyawan. Pemegang aset atau karyawan departemen pemilik melapor
# sendiri; perpindahan biasa langsung tercatat di histori movement, yang krusial (pindah departemen
# pemilik / pindah site) menunggu persetujuan GA.

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..decorators import role_required
from ..models import (
    Asset, AssetAssignment, Department, Employee, Location, MovementReport,
    apply_movement_report, movement_crucial_reasons,
)
from ..roles import is_ga, is_user_role


def _reportable_assets(employee):
    """Aset yang boleh dilaporkan pindah oleh karyawan ini: yang ia pegang atau milik departemennya."""
    allowed = Q(assignments__diserahkan_kepada=employee, assignments__status='active')
    if employee.departemen_id:
        allowed |= Q(departemen_pemilik_id=employee.departemen_id)
    return Asset.objects.exclude(status_siklus_hidup='writeoff').filter(allowed).distinct()


def _holders(asset_ids):
    return dict(AssetAssignment.objects.filter(aset_id__in=asset_ids, status='active')
                .values_list('aset_id', 'diserahkan_kepada_id'))


def _block_reason(asset):
    pending = MovementReport.objects.filter(aset=asset, status='pending').only('id').first()
    if pending:
        return f"Sudah ada laporan pindah (#{pending.pk}) yang menunggu persetujuan GA."
    return None


# ================================================
# KARYAWAN
# ================================================

@role_required(is_user_role)
def movement_report_create(request, aset_id=None):
    employee = request.user.employee
    allowed = _reportable_assets(employee)
    asset_qs = allowed.select_related('lokasi', 'departemen_pemilik', 'kategori_aset')

    held_assets = list(asset_qs.filter(assignments__diserahkan_kepada=employee, assignments__status='active')
                       .order_by('nama_aset'))

    selected = None
    if aset_id:
        selected = asset_qs.filter(pk=aset_id).first()
        if selected is None:
            messages.error(request, "Aset ini tidak bisa Anda laporkan pindah: bukan aset yang Anda pegang atau milik departemen Anda.")
            return redirect('movement_report_create')

    posted = request.POST if request.method == 'POST' else None
    if posted:
        selected = asset_qs.filter(pk=posted.get('aset_id') or 0).first()
        if selected is None:
            messages.error(request, "Pilih aset yang Anda pegang atau milik departemen Anda.")
        elif (reason := _block_reason(selected)):
            messages.warning(request, f"{selected.nama_aset}: {reason}")
            return redirect('movement_report_list')
        else:
            # Masuk tempat titip GA = pengembalian; itu lewat GA, bukan laporan pindah
            lokasi_tujuan   = Location.objects.filter(pk=posted.get('lokasi_tujuan') or 0, tempat_penyimpanan=False).first()
            departemen_tujuan = Department.objects.filter(pk=posted.get('departemen_tujuan') or 0).first()
            holder_id     = posted.get('pemegang_tujuan') or ''
            pemegang_tujuan     = Employee.objects.filter(pk=holder_id, user_profile__isnull=False).first() if holder_id else None
            current_holder = _holders([selected.pk]).get(selected.pk)
            catatan = posted.get('catatan', '').strip()

            if lokasi_tujuan is None:
                messages.error(request, "Pilih lokasi tujuan.", extra_tags='field:lokasi_tujuan|Lokasi tujuan')
            elif holder_id and pemegang_tujuan is None:
                messages.error(request, "Pemegang baru harus karyawan dengan akses Karyawan.", extra_tags='field:pemegang_tujuan|Pemegang')
            elif (lokasi_tujuan.pk == selected.lokasi_id
                  and (departemen_tujuan or selected.departemen_pemilik) == selected.departemen_pemilik
                  and getattr(pemegang_tujuan, 'pk', None) == current_holder):
                messages.error(request, "Tidak ada yang berubah: lokasi, departemen, dan pemegangnya masih sama.")
            else:
                reasons = movement_crucial_reasons(selected, departemen_tujuan, lokasi_tujuan)
                try:
                    with transaction.atomic():
                        report = MovementReport.objects.create(
                            aset=selected, dilaporkan_oleh=employee,
                            lokasi_asal=selected.lokasi, departemen_asal=selected.departemen_pemilik,
                            pemegang_asal_id=current_holder,
                            lokasi_tujuan=lokasi_tujuan, departemen_tujuan=departemen_tujuan or selected.departemen_pemilik,
                            pemegang_tujuan=pemegang_tujuan, catatan=catatan,
                            alasan=' · '.join(reasons), status='pending' if reasons else 'recorded',
                        )
                        if not reasons:
                            apply_movement_report(report, by=employee)
                except IntegrityError:
                    messages.warning(request, f"{selected.nama_aset} sudah punya laporan pindah yang menunggu persetujuan GA.")
                    return redirect('movement_report_list')
                if reasons:
                    messages.success(request, f"Laporan pindah {selected.nama_aset} terkirim. Menunggu persetujuan GA karena {report.alasan.lower()}.")
                else:
                    messages.success(request, f"Perpindahan {selected.nama_aset} tercatat. Lokasi aset sudah diperbarui.")
                return redirect('movement_report_list')

    if selected is not None:
        selected.holder_id = _holders([selected.pk]).get(selected.pk)
    holders = _holders([a.pk for a in held_assets])
    pending = dict(MovementReport.objects.filter(aset__in=held_assets, status='pending').values_list('aset_id', 'id'))
    for a in held_assets:
        a.holder_id = holders.get(a.pk)
        a.pending_report = pending.get(a.pk)

    return render(request, 'user/movement_report_form.html', {
        'held_assets': held_assets,
        'selected':    selected,
        'locations':   Location.objects.filter(tempat_penyimpanan=False).order_by('jalur_lengkap'),
        'departments': Department.objects.order_by('nama'),
        'recipients':  Employee.objects.filter(user_profile__isnull=False, user__is_active=True)
                       .select_related('departemen').order_by('nama'),
        'posted':      posted,
    })


@role_required(is_user_role)
def movement_report_asset_search(request):
    """Cari aset yang boleh dilaporkan pindah (dipegang / milik departemen pelapor). Maks. 10."""
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})
    employee = request.user.employee
    qs = _reportable_assets(employee)
    for term in q.split()[:5]:
        qs = qs.filter(Q(nama_aset__icontains=term) | Q(nomor_aset__icontains=term) |
                       Q(lokasi__jalur_lengkap__icontains=term) | Q(kategori_aset__nama__icontains=term))
    assets = list(qs.select_related('lokasi', 'departemen_pemilik', 'kategori_aset').order_by('nama_aset', 'id')[:10])
    pending = dict(MovementReport.objects.filter(aset__in=assets, status='pending').values_list('aset_id', 'id'))
    holders = _holders([a.pk for a in assets])
    return JsonResponse({'results': [{
        'id':       a.pk,
        'name':     a.nama_aset,
        'number':   a.nomor_aset,
        'category': a.departemen_pemilik.nama if a.departemen_pemilik else '',
        'location': a.lokasi.jalur_lengkap if a.lokasi else 'Belum ditempatkan',
        'tag':      'Aset Anda' if holders.get(a.pk) == employee.pk else '',
        'blocked':  f"Menunggu persetujuan GA (#{pending[a.pk]})" if a.pk in pending else '',
        'data': {
            'dept':     a.departemen_pemilik_id or '',
            'holder':   holders.get(a.pk) or '',
            'site':     (a.lokasi.jalur_lengkap or a.lokasi.nama).split(' > ')[0] if a.lokasi else '',
            'location': a.lokasi_id or '',
        },
    } for a in assets]})


@role_required(is_user_role)
def movement_report_list(request):
    reports = (MovementReport.objects.filter(dilaporkan_oleh=request.user.employee)
               .select_related('aset', 'lokasi_asal', 'lokasi_tujuan', 'departemen_asal', 'departemen_tujuan',
                               'pemegang_asal', 'pemegang_tujuan', 'ditinjau_oleh'))
    page_obj = Paginator(reports, 25).get_page(request.GET.get('page'))
    return render(request, 'user/movement_report_list.html', {'reports': page_obj, 'page_obj': page_obj})


# ================================================
# GA: TINJAU & SETUJUI
# ================================================

GA_TABS = {
    'pending':  Q(status='pending'),
    'recorded': Q(status='recorded'),
    'reviewed': Q(status__in=['approved', 'rejected']),
}


@role_required(is_ga)
def ga_movement_report_list(request):
    tab = request.GET.get('tab', 'pending')
    if tab not in GA_TABS:
        tab = 'pending'
    counts = MovementReport.objects.aggregate(**{key: Count('id', filter=f) for key, f in GA_TABS.items()})
    reports = (MovementReport.objects.filter(GA_TABS[tab])
               .select_related('aset', 'dilaporkan_oleh__departemen', 'lokasi_asal', 'lokasi_tujuan',
                               'departemen_asal', 'departemen_tujuan', 'pemegang_asal', 'pemegang_tujuan', 'ditinjau_oleh'))
    if tab == 'pending':
        reports = reports.order_by('dibuat_pada')  # yang paling lama menunggu di atas
    page_obj = Paginator(reports, 25).get_page(request.GET.get('page'))
    return render(request, 'ga/movement_report_list.html', {
        'reports': page_obj, 'page_obj': page_obj, 'tab': tab, 'counts': counts,
    })


def _review(request, pk):
    return get_object_or_404(MovementReport.objects.select_related('aset', 'dilaporkan_oleh'), pk=pk)


@require_POST
@role_required(is_ga)
def ga_movement_report_approve(request, pk):
    report = _review(request, pk)
    if report.status != 'pending':
        messages.info(request, f"Laporan #{report.pk} sudah ditinjau sebelumnya.")
        return redirect('ga_movement_report_list')
    if report.aset.status_siklus_hidup == 'writeoff':
        messages.error(request, f"{report.aset.nama_aset} sudah di-write off; laporan pindah tidak bisa disetujui.")
        return redirect('ga_movement_report_list')
    with transaction.atomic():
        report.status = 'approved'
        report.ditinjau_oleh = request.user.employee
        report.ditinjau_pada = timezone.now()
        report.catatan_tinjauan = request.POST.get('catatan_tinjauan', '').strip()
        report.save(update_fields=['status', 'ditinjau_oleh', 'ditinjau_pada', 'catatan_tinjauan'])
        apply_movement_report(report, by=request.user.employee)
    messages.success(request, f"Perpindahan {report.aset.nama_aset} disetujui dan tercatat.")
    return redirect('ga_movement_report_list')


@require_POST
@role_required(is_ga)
def ga_movement_report_reject(request, pk):
    report = _review(request, pk)
    if report.status != 'pending':
        messages.info(request, f"Laporan #{report.pk} sudah ditinjau sebelumnya.")
        return redirect('ga_movement_report_list')
    note = request.POST.get('catatan_tinjauan', '').strip()
    if not note:
        messages.error(request, "Tulis alasan penolakan agar pelapor tahu harus bagaimana.")
        return redirect('ga_movement_report_list')
    report.status = 'rejected'
    report.ditinjau_oleh = request.user.employee
    report.ditinjau_pada = timezone.now()
    report.catatan_tinjauan = note
    report.save(update_fields=['status', 'ditinjau_oleh', 'ditinjau_pada', 'catatan_tinjauan'])
    messages.success(request, f"Laporan pindah {report.aset.nama_aset} ditolak. Aset tetap di posisi semula.")
    return redirect('ga_movement_report_list')
