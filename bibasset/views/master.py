# views/master.py
from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import role_required
from ..forms import (
    AssetCategoryForm, AssetGroupForm, DepartmentForm, LocationForm, flash_form_errors,
)
from ..models import Departemen, Lokasi, GrupAset, KategoriAset, bangun_pohon_lokasi
from ..roles import is_ga


def _posted(request):
    return request.POST.dict() if request.method == "POST" else None


def _save_form(request, form, success_msg=None):
    """Simpan form kalau valid; kalau tidak, tampilkan error. Return objek atau None."""
    if form.is_valid():
        obj = form.save()
        if success_msg:
            messages.success(request, success_msg.format(obj=obj))
        return obj
    flash_form_errors(request, form)
    return None


# ===============================
# DEPARTMENT
# ===============================

@role_required(is_ga)
def department_list(request):
    return render(request, "master/department_list.html", {
        "departments": Departemen.objects.annotate(
            emp_count=Count('karyawan', distinct=True), asset_count=Count('aset', distinct=True),
        ).order_by('nama')
    })


@role_required(is_ga)
def department_create(request):
    if request.method == "POST" and _save_form(request, DepartmentForm(request.POST)):
        return redirect("department_list")
    return render(request, "master/department_create.html", {"posted": _posted(request)})


@role_required(is_ga)
def department_delete(request, pk):
    dept = get_object_or_404(Departemen, pk=pk)
    if dept.held_assets.exists():
        messages.error(request, f"Departemen '{dept.nama}' tidak bisa dihapus karena punya riwayat serah terima aset.")
        return redirect("department_list")
    dept.delete()
    return redirect("department_list")


# ===============================
# LOCATION
# ===============================

@role_required(is_ga)
def location_list(request):
    # .assets = aset langsung di lokasi ini, .total_assets = termasuk semua sub-lokasi
    locations = list(Lokasi.objects.annotate(assets=Count('aset')).order_by('jalur_lengkap'))
    tree, _   = bangun_pohon_lokasi(locations, jumlah=('assets',))

    return render(request, "master/location_list.html", {
        "locations": locations,   # untuk bulk-delete form & count
        "tree":      tree,        # untuk render tree accordion
    })

@role_required(is_ga)
def location_create(request):
    parents = Lokasi.objects.order_by('jalur_lengkap')
    preset_parent = request.GET.get('parent', '')
    if request.method == "POST" and _save_form(request, LocationForm(request.POST)):
        return redirect("location_list")
    return render(request, "master/location_create.html", {
        "parents": parents, "posted": _posted(request),
        "preset_parent": int(preset_parent) if preset_parent.isdigit() else None,
    })


@role_required(is_ga)
def location_update(request, pk):
    loc     = get_object_or_404(Lokasi, pk=pk)
    parents = Lokasi.objects.exclude(pk=pk).order_by('jalur_lengkap')
    if request.method == "POST":
        if _save_form(request, LocationForm(request.POST, instance=loc),
                      "Lokasi '{obj.nama}' berhasil diperbarui."):
            return redirect("location_list")
        loc.refresh_from_db()
    return render(request, "master/location_update.html", {"loc": loc, "parents": parents, "posted": _posted(request)})


@role_required(is_ga)
def location_bulk_delete(request):
    if request.method == 'POST':
        ids = request.POST.getlist('selected_ids')
        if ids:
            deleted_count, _ = Lokasi.objects.filter(pk__in=ids).delete()
            messages.success(request, f"{deleted_count} lokasi berhasil dihapus.")
        else:
            messages.warning(request, "Tidak ada lokasi yang dipilih.")
    return redirect('location_list')

@role_required(is_ga)
def location_delete(request, pk):
    get_object_or_404(Lokasi, pk=pk).delete()
    return redirect("location_list")


# ===============================
# ASSET GROUP
# ===============================

@role_required(is_ga)
def assetgroup_list(request):
    return render(request, "master/assetgroup_list.html", {
        # Aset terhubung ke grup lewat kategorinya
        "groups": GrupAset.objects.prefetch_related('categories').annotate(
            asset_count=Count('categories__assets', distinct=True),
        ).order_by('nama')
    })


@role_required(is_ga)
def assetgroup_create(request):
    if request.method == "POST" and _save_form(request, AssetGroupForm(request.POST)):
        return redirect("assetgroup_list")
    return render(request, "master/assetgroup_create.html", {"posted": _posted(request)})


@role_required(is_ga)
def assetgroup_update(request, pk):
    group = get_object_or_404(
        GrupAset.objects.annotate(asset_count=Count('categories__assets', distinct=True)), pk=pk,
    )
    if request.method == "POST":
        if _save_form(request, AssetGroupForm(request.POST, instance=group),
                      "Grup aset '{obj.nama}' berhasil diperbarui."):
            return redirect("assetgroup_list")
        group.refresh_from_db()
    return render(request, "master/assetgroup_update.html", {"group": group, "posted": _posted(request)})


@role_required(is_ga)
def assetgroup_delete(request, pk):
    get_object_or_404(GrupAset, pk=pk).delete()
    return redirect("assetgroup_list")


# ===============================
# ASSET CATEGORY
# ===============================

@role_required(is_ga)
def assetcategory_list(request):
    grup_id = request.GET.get('group')
    categories = KategoriAset.objects.select_related('grup').annotate(
        asset_count=Count('assets')
    ).order_by('grup', 'nama')

    selected_group = None
    if grup_id:
        categories = categories.filter(grup_id=grup_id)
        selected_group = get_object_or_404(GrupAset, pk=grup_id)

    return render(request, "master/assetcategory_list.html", {
        "categories":     categories,
        "groups":         GrupAset.objects.all(),
        "selected_group": selected_group,
    })


@role_required(is_ga)
def assetcategory_create(request):
    if request.method == "POST" and _save_form(request, AssetCategoryForm(request.POST)):
        return redirect("assetcategory_list")
    return render(request, "master/assetcategory_create.html", {
        "groups": GrupAset.objects.all(),
        "posted": _posted(request),
    })


@role_required(is_ga)
def assetcategory_update(request, pk):
    category = get_object_or_404(KategoriAset, pk=pk)
    if request.method == "POST":
        if _save_form(request, AssetCategoryForm(request.POST, instance=category)):
            return redirect("assetcategory_list")
        category.refresh_from_db()
    return render(request, "master/assetcategory_update.html", {
        "category": category,
        "groups":   GrupAset.objects.all(),
        "posted":   _posted(request),
    })


@role_required(is_ga)
def assetcategory_delete(request, pk):
    category = get_object_or_404(KategoriAset, pk=pk)
    count = category.assets.count()
    if count > 0:
        messages.error(
            request,
            f"Kategori '{category.nama}' tidak bisa dihapus karena masih ada {count} aset terdaftar."
        )
        return redirect("assetcategory_list")
    category.delete()
    messages.success(request, f"Kategori '{category.nama}' berhasil dihapus.")
    return redirect("assetcategory_list")