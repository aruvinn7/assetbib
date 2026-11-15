# views/components.py
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, DecimalField, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import role_required
from ..forms import AssetComponentForm, ComponentReplacementForm, flash_form_errors
from ..models import Aset, KomponenAset
from ..roles import is_ga


def _posted(request):
    return request.POST.dict() if request.method == "POST" else None


@role_required(is_ga)
def component_list(request, aset_id):
    asset      = get_object_or_404(Aset, id=aset_id)
    components = asset.components.all()
    # 1 query untuk semua angka ringkasan
    totals = components.aggregate(
        total=Count('id'),
        critical=Count('id', filter=Q(komponen_kritis=True)),
        biaya=Sum(F('harga_estimasi') * F('jumlah'), output_field=DecimalField()),
    )
    return render(request, 'components/list.html', {
        'asset':               asset,
        'components':          components,
        'total_components':    totals['total'],
        'critical_count':      totals['critical'],
        'total_estimated_cost': totals['cost'] or 0,
    })


@role_required(is_ga)
def component_create(request, aset_id):
    asset = get_object_or_404(Aset, id=aset_id)
    if request.method == "POST":
        form = AssetComponentForm(request.POST, instance=KomponenAset(aset=asset))
        if form.is_valid():
            component = form.save(commit=False)
            component.dibuat_oleh = request.user.employee
            component.save()
            return redirect('component_list', aset_id=asset.id)
        flash_form_errors(request, form)
    return render(request, 'components/create.html', {'asset': asset, 'posted': _posted(request)})


@role_required(is_ga)
def component_update(request, pk):
    komponen = get_object_or_404(KomponenAset.objects.select_related('aset'), pk=pk)
    asset    = komponen.aset
    if request.method == "POST":
        form = AssetComponentForm(request.POST, instance=komponen)
        if form.is_valid():
            form.save()
            return redirect('component_list', aset_id=asset.id)
        flash_form_errors(request, form)
        komponen.refresh_from_db()
    return render(request, 'components/update.html', {
        'component': komponen, 'asset': asset, 'posted': _posted(request),
    })


@role_required(is_ga)
def component_delete(request, pk):
    component = get_object_or_404(KomponenAset, pk=pk)
    aset_id  = component.aset_id
    component.delete()
    return redirect('component_list', aset_id=aset_id)


@role_required(is_ga)
def component_detail(request, pk):
    komponen     = get_object_or_404(KomponenAset.objects.select_related('aset'), pk=pk)
    replacements = komponen.replacements.select_related('dilaksanakan_oleh')
    totals       = replacements.aggregate(count=Count('id'), biaya=Sum('biaya'))
    return render(request, 'components/detail.html', {
        'component':            komponen,
        'asset':                komponen.aset,
        'replacements':         replacements,
        'total_replacements':   totals['count'],
        'total_replacement_cost': totals['biaya'] or 0,
    })


@role_required(is_ga)
def replacement_create(request, komponen_id):
    komponen = get_object_or_404(KomponenAset.objects.select_related('aset'), id=komponen_id)
    if request.method == "POST":
        form = ComponentReplacementForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                replacement = form.save(commit=False)
                replacement.komponen    = komponen
                replacement.dilaksanakan_oleh = request.user.employee
                replacement.save()
                komponen.penggantian_terakhir = replacement.tanggal_penggantian
                komponen.save(update_fields=['penggantian_terakhir', 'diperbarui_pada'])
            messages.success(request, "Penggantian komponen berhasil dicatat.")
            return redirect('component_detail', pk=komponen.id)
        flash_form_errors(request, form)
    return render(request, 'components/replacement_create.html', {
        'component': komponen,
        'asset':     komponen.aset,
        'posted':    _posted(request),
    })
