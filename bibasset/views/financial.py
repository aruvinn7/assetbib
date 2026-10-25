# views/financial.py
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import role_required
from ..forms import AssetFinancialForm, flash_form_errors
from ..models import Asset, AssetFinancial
from ..roles import is_finance


def _get_asset(asset_id):
    return get_object_or_404(
        Asset.objects.select_related('financial', 'lokasi_asset', 'asset_group', 'asset_category'),
        id=asset_id,
    )


@role_required(is_finance)
def financial_create(request, asset_id):
    asset = _get_asset(asset_id)
    if hasattr(asset, "financial"):
        return redirect("financial_detail", asset.id)

    if request.method == "POST":
        form = AssetFinancialForm(request.POST, instance=AssetFinancial(asset=asset))
        if form.is_valid():
            financial = form.save(commit=False)
            financial.created_by = request.user.employee
            financial.save()
            return redirect("financial_detail", asset.id)
        flash_form_errors(request, form)
    return render(request, "financial/create.html", {
        "asset": asset,
        "posted": request.POST.dict() if request.method == "POST" else None,
    })


@role_required(is_finance)
def financial_update(request, asset_id):
    asset = _get_asset(asset_id)
    if not hasattr(asset, "financial"):
        return redirect("financial_create", asset.id)
    financial = asset.financial

    if request.method == "POST":
        form = AssetFinancialForm(request.POST, instance=financial)
        if form.is_valid():
            form.save()
            return redirect("financial_detail", asset.id)
        flash_form_errors(request, form)
        financial.refresh_from_db()
    return render(request, "financial/update.html", {
        "asset": asset, "financial": financial,
        "posted": request.POST.dict() if request.method == "POST" else None,
    })


@role_required(is_finance)
def financial_detail(request, asset_id):
    asset = _get_asset(asset_id)
    if not hasattr(asset, "financial"):
        return redirect("financial_create", asset.id)
    return render(request, "financial/detail.html", {"asset": asset, "financial": asset.financial})
