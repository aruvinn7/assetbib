# views/components.py
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, DecimalField, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import role_required
from ..forms import AssetComponentForm, ComponentReplacementForm, flash_form_errors
from ..models import Asset, AssetComponent
from ..roles import is_ga


def _posted(request):
    return request.POST.dict() if request.method == "POST" else None


@role_required(is_ga)
def component_list(request, asset_id):
    asset      = get_object_or_404(Asset, id=asset_id)
    components = asset.components.all()
    # 1 query untuk semua angka ringkasan
    totals = components.aggregate(
        total=Count('id'),
        critical=Count('id', filter=Q(is_critical=True)),
        cost=Sum(F('estimated_price') * F('quantity'), output_field=DecimalField()),
    )
    return render(request, 'components/list.html', {
        'asset':               asset,
        'components':          components,
        'total_components':    totals['total'],
        'critical_count':      totals['critical'],
        'total_estimated_cost': totals['cost'] or 0,
    })


@role_required(is_ga)
def component_create(request, asset_id):
    asset = get_object_or_404(Asset, id=asset_id)
    if request.method == "POST":
        form = AssetComponentForm(request.POST, instance=AssetComponent(asset=asset))
        if form.is_valid():
            component = form.save(commit=False)
            component.created_by = request.user.employee
            component.save()
            return redirect('component_list', asset_id=asset.id)
        flash_form_errors(request, form)
    return render(request, 'components/create.html', {'asset': asset, 'posted': _posted(request)})


@role_required(is_ga)
def component_update(request, pk):
    component = get_object_or_404(AssetComponent.objects.select_related('asset'), pk=pk)
    asset     = component.asset
    if request.method == "POST":
        form = AssetComponentForm(request.POST, instance=component)
        if form.is_valid():
            form.save()
            return redirect('component_list', asset_id=asset.id)
        flash_form_errors(request, form)
        component.refresh_from_db()
    return render(request, 'components/update.html', {
        'component': component, 'asset': asset, 'posted': _posted(request),
    })


@role_required(is_ga)
def component_delete(request, pk):
    component = get_object_or_404(AssetComponent, pk=pk)
    asset_id  = component.asset_id
    component.delete()
    return redirect('component_list', asset_id=asset_id)


@role_required(is_ga)
def component_detail(request, pk):
    component    = get_object_or_404(AssetComponent.objects.select_related('asset'), pk=pk)
    replacements = component.replacements.select_related('performed_by')
    totals       = replacements.aggregate(count=Count('id'), cost=Sum('cost'))
    return render(request, 'components/detail.html', {
        'component':             component,
        'asset':                 component.asset,
        'replacements':          replacements,
        'total_replacements':    totals['count'],
        'total_replacement_cost': totals['cost'] or 0,
    })


@role_required(is_ga)
def replacement_create(request, component_id):
    component = get_object_or_404(AssetComponent.objects.select_related('asset'), id=component_id)
    if request.method == "POST":
        form = ComponentReplacementForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                replacement = form.save(commit=False)
                replacement.component    = component
                replacement.performed_by = request.user.employee
                replacement.save()
                component.last_replaced = replacement.replaced_date
                component.save(update_fields=['last_replaced', 'updated_at'])
            messages.success(request, "Penggantian komponen berhasil dicatat.")
            return redirect('component_detail', pk=component.id)
        flash_form_errors(request, form)
    return render(request, 'components/replacement_create.html', {
        'component': component,
        'asset':     component.asset,
        'posted':    _posted(request),
    })
