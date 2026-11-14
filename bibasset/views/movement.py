# views/movement.py
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Exists, F, Max, OuterRef, Prefetch, Q, prefetch_related_objects
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from ..decorators import role_required
from ..forms import MovementForm, flash_form_errors
from ..models import Asset, AssetMovement, Department, Employee, Location
from ..models import record_movement, sync_holder
from ..roles import is_ga


def _back(request, aset_id):
    """Kembali ke halaman & saringan riwayat yang tadi dibuka, dengan aset ini terbuka (#mv-<id>)."""
    url = request.META.get('HTTP_REFERER', '')
    base = reverse('movement_dashboard')
    if not (url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()})
            and base in url):
        url = base
    return redirect(url.split('#')[0] + f'#mv-{aset_id}')


# ── Dashboard: list aset + lokasi & status terkini ───────────────

@role_required(is_ga)
def movement_dashboard(request):
    """
    Histori movement per aset (accordion), dipaginasi 25 aset per halaman.
    Urut dari perpindahan terbaru; aset yang belum pernah dicatat di paling bawah.
    Search & filter jalan di server supaya berlaku ke semua halaman.
    Form tambah/edit dirender SEKALI dan dipakai ulang lewat JS.
    """
    search    = request.GET.get('q', '').strip()
    status    = request.GET.get('status', '')
    mv_filter = request.GET.get('mv', '')

    has_movement = Exists(AssetMovement.objects.filter(aset=OuterRef('pk')))
    assets_qs = Asset.objects.select_related(
        'lokasi', 'kategori_aset__grup',
    ).annotate(
        has_movement=has_movement,
        last_moved=Max('movements__tanggal_masuk'),
    ).order_by(F('last_moved').desc(nulls_last=True), 'nama_aset')

    if search:
        assets_qs = assets_qs.filter(
            Q(nama_aset__icontains=search) |
            Q(nomor_aset__icontains=search) |
            Q(lokasi__nama__icontains=search)
        )
    if status in ['active', 'maintenance', 'writeoff']:
        assets_qs = assets_qs.filter(status_siklus_hidup=status)

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
            'dipegang_oleh', 'departemen', 'lokasi', 'dicatat_oleh'
        ).order_by('-tanggal_masuk'),
    ))
    for asset in page_assets:
        all_movements = list(asset.movements.all())
        asset.current_movement = next((m for m in all_movements if m.posisi_saat_ini), None)
        asset.movement_history = all_movements
        asset.movement_count   = len(all_movements)

    return render(request, 'dashboard/movement.html', {
        'assets':          page_assets,
        'page_obj':        page_obj,
        'stats':           stats,
        'search':          search,
        'status_filter':   status,
        'movement_filter': mv_filter or 'all',
        'locations':   Location.objects.only('id', 'nama', 'jalur_lengkap').order_by('jalur_lengkap'),
        'departments': Department.objects.order_by('nama'),
        'employees':   Employee.objects.select_related('departemen').order_by('nama'),
    })


# ── Create / update / delete ─────────────────────────────────────

@role_required(is_ga)
@require_POST
def movement_create(request, aset_id):
    asset = get_object_or_404(Asset, pk=aset_id)

    if asset.status_siklus_hidup == 'writeoff':
        messages.error(request, f"Aset '{asset.nama_aset}' sudah di-write off; perpindahan tidak bisa dicatat.")
        return _back(request, asset.pk)

    form = MovementForm(request.POST)
    if not form.is_valid():
        flash_form_errors(request, form)
        return _back(request, asset.pk)

    record_movement(
        asset=asset,
        dipegang_oleh=form.cleaned_data['dipegang_oleh'],
        departemen=form.cleaned_data['departemen'],
        lokasi=form.cleaned_data['lokasi'],
        dicatat_oleh=request.user.employee,
        catatan=form.cleaned_data['catatan'],
    )
    messages.success(request, f"Perpindahan '{asset.nama_aset}' dicatat.")
    return _back(request, asset.pk)


@role_required(is_ga)
@require_POST
def movement_update(request, pk):
    mv = get_object_or_404(AssetMovement.objects.select_related('aset'), pk=pk)

    if mv.aset.status_siklus_hidup == 'writeoff':
        messages.error(request, "Catatan perpindahan aset write off tidak bisa diubah.")
        return _back(request, mv.aset_id)

    form = MovementForm(request.POST)
    if not form.is_valid():
        flash_form_errors(request, form)
        return _back(request, mv.aset_id)

    with transaction.atomic():
        mv.dipegang_oleh    = form.cleaned_data['dipegang_oleh']
        mv.departemen = form.cleaned_data['departemen']
        mv.lokasi   = form.cleaned_data['lokasi']
        mv.catatan      = form.cleaned_data['catatan']
        mv.save()
        # Record aktif = posisi aset sekarang → sinkronkan lokasi & pemegang aset
        if mv.posisi_saat_ini:
            if mv.lokasi_id:
                Asset.objects.filter(pk=mv.aset_id).update(lokasi_id=mv.lokasi_id)
            sync_holder(mv.aset, mv.dipegang_oleh, oleh=request.user.employee, catatan=mv.catatan,
                        departemen=mv.departemen, lokasi=mv.lokasi)
    messages.success(request, "Catatan perpindahan diperbarui.")
    return _back(request, mv.aset_id)


@role_required(is_ga)
@require_POST
def movement_delete(request, pk):
    mv = get_object_or_404(AssetMovement.objects.select_related('aset'), pk=pk)

    if mv.aset.status_siklus_hidup == 'writeoff':
        messages.error(request, "Catatan perpindahan aset write off tidak bisa dihapus.")
        return _back(request, mv.aset_id)

    with transaction.atomic():
        was_current = mv.posisi_saat_ini
        asset       = mv.aset
        mv.delete()
        # Kalau yang dihapus record aktif, record sebelumnya jadi aktif lagi (beserta pemegangnya)
        if was_current:
            prev = asset.movements.select_related('dipegang_oleh', 'departemen', 'lokasi').order_by('-tanggal_masuk').first()
            if prev:
                prev.posisi_saat_ini = True
                prev.tanggal_keluar  = None
                prev.save(update_fields=['posisi_saat_ini', 'tanggal_keluar'])
            sync_holder(asset, prev.dipegang_oleh if prev else None, oleh=request.user.employee,
                        departemen=prev.departemen if prev else None, lokasi=prev.lokasi if prev else None)
    messages.success(request, f"Catatan perpindahan '{asset.nama_aset}' dihapus.")
    return _back(request, asset.pk)
