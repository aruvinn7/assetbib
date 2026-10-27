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
    allowed = Q(assignments__assigned_to=employee, assignments__status='active')
    if employee.department_id:
        allowed |= Q(owned_by_department_id=employee.department_id)
    return Asset.objects.exclude(lifecycle_status='writeoff').filter(allowed).distinct()


def _holders(asset_ids):
    return dict(AssetAssignment.objects.filter(asset_id__in=asset_ids, status='active')
                .values_list('asset_id', 'assigned_to_id'))


def _block_reason(asset):
    pending = MovementReport.objects.filter(asset=asset, status='pending').only('id').first()
    if pending:
        return f"Sudah ada laporan pindah (#{pending.pk}) yang menunggu persetujuan GA."
    return None


# ================================================
# KARYAWAN
# ================================================

@role_required(is_user_role)
def movement_report_create(request, asset_id=None):
    employee = request.user.employee
    allowed = _reportable_assets(employee)
    asset_qs = allowed.select_related('lokasi_asset', 'owned_by_department', 'asset_category')

    held_assets = list(asset_qs.filter(assignments__assigned_to=employee, assignments__status='active')
                       .order_by('nama_asset'))

    selected = None
    if asset_id:
        selected = asset_qs.filter(pk=asset_id).first()
        if selected is None:
            messages.error(request, "Aset ini tidak bisa Anda laporkan pindah: bukan aset yang Anda pegang atau milik departemen Anda.")
            return redirect('movement_report_create')

    posted = request.POST if request.method == 'POST' else None
    if posted:
        selected = asset_qs.filter(pk=posted.get('asset_id') or 0).first()
        if selected is None:
            messages.error(request, "Pilih aset yang Anda pegang atau milik departemen Anda.")
        elif (reason := _block_reason(selected)):
            messages.warning(request, f"{selected.nama_asset}: {reason}")
            return redirect('movement_report_list')
        else:
            to_location   = Location.objects.filter(pk=posted.get('to_location') or 0).first()
            to_department = Department.objects.filter(pk=posted.get('to_department') or 0).first()
            holder_id     = posted.get('to_holder') or ''
            to_holder     = Employee.objects.filter(pk=holder_id, user_profile__isnull=False).first() if holder_id else None
            current_holder = _holders([selected.pk]).get(selected.pk)
            notes = posted.get('notes', '').strip()

            if to_location is None:
                messages.error(request, "Pilih lokasi tujuan.", extra_tags='field:to_location|Lokasi tujuan')
            elif holder_id and to_holder is None:
                messages.error(request, "Pemegang baru harus karyawan dengan akses Karyawan.", extra_tags='field:to_holder|Pemegang')
            elif (to_location.pk == selected.lokasi_asset_id
                  and (to_department or selected.owned_by_department) == selected.owned_by_department
                  and getattr(to_holder, 'pk', None) == current_holder):
                messages.error(request, "Tidak ada yang berubah: lokasi, departemen, dan pemegangnya masih sama.")
            else:
                reasons = movement_crucial_reasons(selected, to_department, to_location)
                try:
                    with transaction.atomic():
                        report = MovementReport.objects.create(
                            asset=selected, reported_by=employee,
                            from_location=selected.lokasi_asset, from_department=selected.owned_by_department,
                            from_holder_id=current_holder,
                            to_location=to_location, to_department=to_department or selected.owned_by_department,
                            to_holder=to_holder, notes=notes,
                            reasons=' · '.join(reasons), status='pending' if reasons else 'recorded',
                        )
                        if not reasons:
                            apply_movement_report(report, by=employee)
                except IntegrityError:
                    messages.warning(request, f"{selected.nama_asset} sudah punya laporan pindah yang menunggu persetujuan GA.")
                    return redirect('movement_report_list')
                if reasons:
                    messages.success(request, f"Laporan pindah {selected.nama_asset} terkirim. Menunggu persetujuan GA karena {report.reasons.lower()}.")
                else:
                    messages.success(request, f"Perpindahan {selected.nama_asset} tercatat. Lokasi aset sudah diperbarui.")
                return redirect('movement_report_list')

    if selected is not None:
        selected.holder_id = _holders([selected.pk]).get(selected.pk)
    holders = _holders([a.pk for a in held_assets])
    pending = dict(MovementReport.objects.filter(asset__in=held_assets, status='pending').values_list('asset_id', 'id'))
    for a in held_assets:
        a.holder_id = holders.get(a.pk)
        a.pending_report = pending.get(a.pk)

    return render(request, 'user/movement_report_form.html', {
        'held_assets': held_assets,
        'selected':    selected,
        'locations':   Location.objects.order_by('full_path'),
        'departments': Department.objects.order_by('name'),
        'recipients':  Employee.objects.filter(user_profile__isnull=False, user__is_active=True)
                       .select_related('department').order_by('nama'),
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
        qs = qs.filter(Q(nama_asset__icontains=term) | Q(asset_number__icontains=term) |
                       Q(lokasi_asset__full_path__icontains=term) | Q(asset_category__name__icontains=term))
    assets = list(qs.select_related('lokasi_asset', 'owned_by_department', 'asset_category').order_by('nama_asset', 'id')[:10])
    pending = dict(MovementReport.objects.filter(asset__in=assets, status='pending').values_list('asset_id', 'id'))
    holders = _holders([a.pk for a in assets])
    return JsonResponse({'results': [{
        'id':       a.pk,
        'name':     a.nama_asset,
        'number':   a.asset_number,
        'category': a.owned_by_department.name if a.owned_by_department else '',
        'location': a.lokasi_asset.full_path if a.lokasi_asset else 'Belum ditempatkan',
        'tag':      'Aset Anda' if holders.get(a.pk) == employee.pk else '',
        'blocked':  f"Menunggu persetujuan GA (#{pending[a.pk]})" if a.pk in pending else '',
        'data': {
            'dept':     a.owned_by_department_id or '',
            'holder':   holders.get(a.pk) or '',
            'site':     (a.lokasi_asset.full_path or a.lokasi_asset.name).split(' > ')[0] if a.lokasi_asset else '',
            'location': a.lokasi_asset_id or '',
        },
    } for a in assets]})


@role_required(is_user_role)
def movement_report_list(request):
    reports = (MovementReport.objects.filter(reported_by=request.user.employee)
               .select_related('asset', 'from_location', 'to_location', 'from_department', 'to_department',
                               'from_holder', 'to_holder', 'reviewed_by'))
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
               .select_related('asset', 'reported_by__department', 'from_location', 'to_location',
                               'from_department', 'to_department', 'from_holder', 'to_holder', 'reviewed_by'))
    if tab == 'pending':
        reports = reports.order_by('created_at')  # yang paling lama menunggu di atas
    page_obj = Paginator(reports, 25).get_page(request.GET.get('page'))
    return render(request, 'ga/movement_report_list.html', {
        'reports': page_obj, 'page_obj': page_obj, 'tab': tab, 'counts': counts,
    })


def _review(request, pk):
    return get_object_or_404(MovementReport.objects.select_related('asset', 'reported_by'), pk=pk)


@require_POST
@role_required(is_ga)
def ga_movement_report_approve(request, pk):
    report = _review(request, pk)
    if report.status != 'pending':
        messages.info(request, f"Laporan #{report.pk} sudah ditinjau sebelumnya.")
        return redirect('ga_movement_report_list')
    if report.asset.lifecycle_status == 'writeoff':
        messages.error(request, f"{report.asset.nama_asset} sudah di-write off; laporan pindah tidak bisa disetujui.")
        return redirect('ga_movement_report_list')
    with transaction.atomic():
        report.status = 'approved'
        report.reviewed_by = request.user.employee
        report.reviewed_at = timezone.now()
        report.review_notes = request.POST.get('review_notes', '').strip()
        report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes'])
        apply_movement_report(report, by=request.user.employee)
    messages.success(request, f"Perpindahan {report.asset.nama_asset} disetujui dan tercatat.")
    return redirect('ga_movement_report_list')


@require_POST
@role_required(is_ga)
def ga_movement_report_reject(request, pk):
    report = _review(request, pk)
    if report.status != 'pending':
        messages.info(request, f"Laporan #{report.pk} sudah ditinjau sebelumnya.")
        return redirect('ga_movement_report_list')
    note = request.POST.get('review_notes', '').strip()
    if not note:
        messages.error(request, "Tulis alasan penolakan agar pelapor tahu harus bagaimana.")
        return redirect('ga_movement_report_list')
    report.status = 'rejected'
    report.reviewed_by = request.user.employee
    report.reviewed_at = timezone.now()
    report.review_notes = note
    report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes'])
    messages.success(request, f"Laporan pindah {report.asset.nama_asset} ditolak. Aset tetap di posisi semula.")
    return redirect('ga_movement_report_list')
