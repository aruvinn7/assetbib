# views/master.py
from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import role_required
from ..forms import (
    AssetCategoryForm, AssetGroupForm, DepartmentForm, LocationForm, flash_form_errors,
)
from ..models import Department, Location, AssetGroup, AssetCategory
from ..roles import is_ga
from .utils import is_ga_or_finance


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

@role_required(is_ga_or_finance)
def department_list(request):
    return render(request, "master/department_list.html", {
        "departments": Department.objects.annotate(
            emp_count=Count('employee', distinct=True), asset_count=Count('asset', distinct=True),
        ).order_by('name')
    })


@role_required(is_ga_or_finance)
def department_create(request):
    if request.method == "POST" and _save_form(request, DepartmentForm(request.POST)):
        return redirect("department_list")
    return render(request, "master/department_create.html", {"posted": _posted(request)})


@role_required(is_ga)
def department_delete(request, pk):
    get_object_or_404(Department, pk=pk).delete()
    return redirect("department_list")


# ===============================
# LOCATION
# ===============================

def build_location_tree(locations):
    """
    Ubah flat QuerySet → nested dict tree.

    Return: list of root nodes, each node = {
        'obj':      Location instance,
        'children': [node, node, ...]   # rekursif
    }

    Kompleksitas: O(n) — cukup 1 query all locations.
    """
    # Map pk → node
    node_map = {loc.pk: {'obj': loc, 'children': []} for loc in locations}

    roots = []
    for loc in locations:
        if loc.parent_id and loc.parent_id in node_map:
            node_map[loc.parent_id]['children'].append(node_map[loc.pk])
        elif not loc.parent_id:
            roots.append(node_map[loc.pk])

    return roots


# ── View: location_list (ganti yang lama) ───────────────────────────────────

@role_required(is_ga_or_finance)
def location_list(request):
    # select_related parent supaya __str__ tidak N+1
    locations = Location.objects.select_related('parent').annotate(asset_count=Count('asset')).order_by('full_path')
    tree      = build_location_tree(locations)
    # Jumlah aset termasuk semua sub-lokasi
    def _total(node):
        node['total'] = node['obj'].asset_count + sum(_total(ch) for ch in node['children'])
        return node['total']
    for root in tree:
        _total(root)

    return render(request, "master/location_list.html", {
        "locations": locations,   # untuk bulk-delete form & count
        "tree":      tree,        # untuk render tree accordion
    })

@role_required(is_ga_or_finance)
def location_create(request):
    parents = Location.objects.order_by('full_path')
    preset_parent = request.GET.get('parent', '')
    if request.method == "POST" and _save_form(request, LocationForm(request.POST)):
        return redirect("location_list")
    return render(request, "master/location_create.html", {
        "parents": parents, "posted": _posted(request),
        "preset_parent": int(preset_parent) if preset_parent.isdigit() else None,
    })


@role_required(is_ga_or_finance)
def location_update(request, pk):
    loc     = get_object_or_404(Location, pk=pk)
    parents = Location.objects.exclude(pk=pk).order_by('full_path')
    if request.method == "POST":
        if _save_form(request, LocationForm(request.POST, instance=loc),
                      "Lokasi '{obj.name}' berhasil diperbarui."):
            return redirect("location_list")
        loc.refresh_from_db()
    return render(request, "master/location_update.html", {"loc": loc, "parents": parents, "posted": _posted(request)})


@role_required(is_ga)
def location_bulk_delete(request):
    if request.method == 'POST':
        ids = request.POST.getlist('selected_ids')
        if ids:
            deleted_count, _ = Location.objects.filter(pk__in=ids).delete()
            messages.success(request, f"{deleted_count} lokasi berhasil dihapus.")
        else:
            messages.warning(request, "Tidak ada lokasi yang dipilih.")
    return redirect('location_list')

@role_required(is_ga)
def location_delete(request, pk):
    get_object_or_404(Location, pk=pk).delete()
    return redirect("location_list")


# ===============================
# ASSET GROUP
# ===============================

@role_required(is_ga_or_finance)
def assetgroup_list(request):
    return render(request, "master/assetgroup_list.html", {
        # Aset terhubung ke grup lewat kategorinya
        "groups": AssetGroup.objects.prefetch_related('categories').annotate(
            asset_count=Count('categories__assets', distinct=True),
        ).order_by('name')
    })


@role_required(is_ga_or_finance)
def assetgroup_create(request):
    if request.method == "POST" and _save_form(request, AssetGroupForm(request.POST)):
        return redirect("assetgroup_list")
    return render(request, "master/assetgroup_create.html", {"posted": _posted(request)})


@role_required(is_ga_or_finance)
def assetgroup_update(request, pk):
    group = get_object_or_404(
        AssetGroup.objects.annotate(asset_count=Count('categories__assets', distinct=True)), pk=pk,
    )
    if request.method == "POST":
        if _save_form(request, AssetGroupForm(request.POST, instance=group),
                      "Asset Group '{obj.name}' berhasil diperbarui."):
            return redirect("assetgroup_list")
        group.refresh_from_db()
    return render(request, "master/assetgroup_update.html", {"group": group, "posted": _posted(request)})


@role_required(is_ga)
def assetgroup_delete(request, pk):
    get_object_or_404(AssetGroup, pk=pk).delete()
    return redirect("assetgroup_list")


# ===============================
# ASSET CATEGORY
# ===============================

@role_required(is_ga_or_finance)
def assetcategory_list(request):
    group_id = request.GET.get('group')
    categories = AssetCategory.objects.select_related('group').annotate(
        asset_count=Count('assets')
    ).order_by('group', 'name')

    selected_group = None
    if group_id:
        categories = categories.filter(group_id=group_id)
        selected_group = get_object_or_404(AssetGroup, pk=group_id)

    return render(request, "master/assetcategory_list.html", {
        "categories":     categories,
        "groups":         AssetGroup.objects.all(),
        "selected_group": selected_group,
    })


@role_required(is_ga_or_finance)
def assetcategory_create(request):
    if request.method == "POST" and _save_form(request, AssetCategoryForm(request.POST)):
        return redirect("assetcategory_list")
    return render(request, "master/assetcategory_create.html", {
        "groups": AssetGroup.objects.all(),
        "posted": _posted(request),
    })


@role_required(is_ga_or_finance)
def assetcategory_update(request, pk):
    category = get_object_or_404(AssetCategory, pk=pk)
    if request.method == "POST":
        if _save_form(request, AssetCategoryForm(request.POST, instance=category)):
            return redirect("assetcategory_list")
        category.refresh_from_db()
    return render(request, "master/assetcategory_update.html", {
        "category": category,
        "groups":   AssetGroup.objects.all(),
        "posted":   _posted(request),
    })


@role_required(is_ga)
def assetcategory_delete(request, pk):
    category = get_object_or_404(AssetCategory, pk=pk)
    count = category.assets.count()
    if count > 0:
        messages.error(
            request,
            f"Kategori '{category.name}' tidak bisa dihapus karena masih ada {count} aset terdaftar."
        )
        return redirect("assetcategory_list")
    category.delete()
    messages.success(request, f"Kategori '{category.name}' berhasil dihapus.")
    return redirect("assetcategory_list")