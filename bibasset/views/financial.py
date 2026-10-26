# views/financial.py
from datetime import date

from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import role_required
from ..forms import AssetFinancialForm, flash_form_errors
from ..models import Asset, AssetFinancial
from ..roles import is_finance


def _get_asset(asset_id):
    return get_object_or_404(
        Asset.objects.select_related('financial', 'lokasi_asset', 'asset_group', 'asset_category', 'write_off'),
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
    fin = asset.financial
    # Jadwal penyusutan per tahun buku sampai nilai buku mencapai residual
    rows, start_year = [], asset.tanggal_pembelian.year
    end_cap = getattr(asset, 'write_off', None).tanggal if asset.lifecycle_status == 'writeoff' and hasattr(asset, 'write_off') else None
    for year in range(start_year, start_year + fin.umur_ekonomis + 1):
        start = fin.book_value(max(date(year, 1, 1), asset.tanggal_pembelian))
        end = fin.book_value(min(date(year, 12, 31), end_cap) if end_cap else date(year, 12, 31))
        rows.append({'year': year, 'start': start, 'dep': start - end, 'end': end,
                     'current': year == date.today().year, 'past': year < date.today().year})
        if end <= fin.nilai_residual or (end_cap and year >= end_cap.year):
            break
    book = fin.book_value()
    rentang = fin.nilai_awal_aset - fin.nilai_residual
    return render(request, "financial/detail.html", {
        "asset": asset, "financial": fin, "book": book, "rows": rows,
        "book_pct": round(book / fin.nilai_awal_aset * 100) if fin.nilai_awal_aset else 0,
        "dep_pct": round((fin.nilai_awal_aset - book) / rentang * 100) if rentang > 0 else 100,
        "end_date": asset.tanggal_pembelian.replace(year=asset.tanggal_pembelian.year + fin.umur_ekonomis)
                    if not (asset.tanggal_pembelian.month == 2 and asset.tanggal_pembelian.day == 29)
                    else date(asset.tanggal_pembelian.year + fin.umur_ekonomis, 3, 1),
    })
