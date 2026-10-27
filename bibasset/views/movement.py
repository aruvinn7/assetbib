# views/movement.py
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch, Q, prefetch_related_objects
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from ..decorators import role_required
from ..forms import MovementForm, flash_form_errors
from ..models import Asset, AssetMovement, Department, Employee, Location
from ..models import record_movement, sync_holder
from .utils import is_ga_or_finance
from ..roles import is_ga


def _back(request, asset_id):
    """Kembali ke halaman & saringan riwayat yang tadi dibuka, dengan aset ini terbuka (#mv-<id>)."""
    url = request.META.get('HTTP_REFERER', '')
    base = reverse('movement_dashboard')
    if not (url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()})
            and base in url):
        url = base
    return redirect(url.split('#')[0] + f'#mv-{asset_id}')


# ── Dashboard: list aset + lokasi & status terkini ───────────────

@role_required(is_ga_or_finance)
def movement_dashboard(request):
    """
    Histori movement per aset (accordion), dipaginasi 25 aset per halaman.
    Search & filter jalan di server supaya berlaku ke semua halaman.
    Form tambah/edit dirender SEKALI dan dipakai ulang lewat JS.
    """
    search    = request.GET.get('q', '').strip()
    status    = request.GET.get('status', '')
    mv_filter = request.GET.get('mv', '')

    has_movement = Exists(AssetMovement.objects.filter(asset=OuterRef('pk')))
    assets_qs = Asset.objects.select_related(
        'lokasi_asset', 'asset_category__group',
    ).annotate(has_movement=has_movement).order_by('nama_asset')

    if search:
        assets_qs = assets_qs.filter(
            Q(nama_asset__icontains=search) |
            Q(asset_number__icontains=search) |
            Q(lokasi_asset__name__icontains=search)
        )
    if status in ['active', 'maintenance', 'writeoff']:
        assets_qs = assets_qs.filter(lifecycle_status=status)

    # Catatan: Count(filter=Q(has_movement=True)) di aggregate salah hitung untuk
    # anotasi Exists, jadi pakai 2 COUNT terpisah
    stats = {
        'total':        assets_qs.count(),
        'has_movement': assets_qs.filter(has_movement=True).count(),
    }
    stats['no_movement'] = stats['total'] - stats['has_movement']

    if mv_filter == 'has':
        assets_qs = assets_qs.filter(has_movement=True)
    elif mv_filter == 'no':
        assets_qs = assets_qs.filter(has_movement=False)

    paginator = Paginator(assets_qs, 25)
    page_obj  = paginator.get_page(request.GET.get('page'))

    # Prefetch movement hanya untuk aset di halaman ini
    page_assets = list(page_obj.object_list)
    prefetch_related_objects(page_assets, Prefetch(
        'movements',
        queryset=AssetMovement.objects.select_related(
            'held_by', 'department', 'location', 'recorded_by'
        ).order_by('-moved_in'),
    ))
    for asset in page_assets:
        all_movements = list(asset.movements.all())
        asset.current_movement = next((m for m in all_movements if m.is_current), None)
        asset.movement_history = all_movements
        asset.movement_count   = len(all_movements)

    return render(request, 'dashboard/movement.html', {
        'assets':          page_assets,
        'page_obj':        page_obj,
        'stats':           stats,
        'search':          search,
        'status_filter':   status,
        'movement_filter': mv_filter or 'all',
        'locations':   Location.objects.only('id', 'name', 'full_path').order_by('full_path'),
        'departments': Department.objects.order_by('name'),
        'employees':   Employee.objects.select_related('department').order_by('nama'),
    })


# ── Create / update / delete ─────────────────────────────────────

@role_required(is_ga)
@require_POST
def movement_create(request, asset_id):
    asset = get_object_or_404(Asset, pk=asset_id)

    if asset.lifecycle_status == 'writeoff':
        messages.error(request, f"Aset '{asset.nama_asset}' sudah di-write off; perpindahan tidak bisa dicatat.")
        return _back(request, asset.pk)

    form = MovementForm(request.POST)
    if not form.is_valid():
        flash_form_errors(request, form)
        return _back(request, asset.pk)

    record_movement(
        asset=asset,
        held_by=form.cleaned_data['held_by'],
        department=form.cleaned_data['department'],
        location=form.cleaned_data['location'],
        recorded_by=request.user.employee,
        notes=form.cleaned_data['notes'],
    )
    messages.success(request, f"Perpindahan '{asset.nama_asset}' dicatat.")
    return _back(request, asset.pk)


@role_required(is_ga)
@require_POST
def movement_update(request, pk):
    mv = get_object_or_404(AssetMovement.objects.select_related('asset'), pk=pk)

    if mv.asset.lifecycle_status == 'writeoff':
        messages.error(request, "Catatan perpindahan aset write off tidak bisa diubah.")
        return _back(request, mv.asset_id)

    form = MovementForm(request.POST)
    if not form.is_valid():
        flash_form_errors(request, form)
        return _back(request, mv.asset_id)

    with transaction.atomic():
        mv.held_by    = form.cleaned_data['held_by']
        mv.department = form.cleaned_data['department']
        mv.location   = form.cleaned_data['location']
        mv.notes      = form.cleaned_data['notes']
        mv.save()
        # Record aktif = posisi aset sekarang → sinkronkan lokasi & pemegang aset
        if mv.is_current:
            if mv.location_id:
                Asset.objects.filter(pk=mv.asset_id).update(lokasi_asset_id=mv.location_id)
            sync_holder(mv.asset, mv.held_by, by=request.user.employee, notes=mv.notes)
    messages.success(request, "Catatan perpindahan diperbarui.")
    return _back(request, mv.asset_id)


@role_required(is_ga)
@require_POST
def movement_delete(request, pk):
    mv = get_object_or_404(AssetMovement.objects.select_related('asset'), pk=pk)

    if mv.asset.lifecycle_status == 'writeoff':
        messages.error(request, "Catatan perpindahan aset write off tidak bisa dihapus.")
        return _back(request, mv.asset_id)

    with transaction.atomic():
        was_current = mv.is_current
        asset       = mv.asset
        mv.delete()
        # Kalau yang dihapus record aktif, record sebelumnya jadi aktif lagi (beserta pemegangnya)
        if was_current:
            prev = asset.movements.select_related('held_by').order_by('-moved_in').first()
            if prev:
                prev.is_current = True
                prev.moved_out  = None
                prev.save(update_fields=['is_current', 'moved_out'])
            sync_holder(asset, prev.held_by if prev else None, by=request.user.employee)
    messages.success(request, f"Catatan perpindahan '{asset.nama_asset}' dihapus.")
    return _back(request, asset.pk)
