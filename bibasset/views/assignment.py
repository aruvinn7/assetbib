# views/assignment.py
from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import role_required
from ..forms import AssignmentForm, flash_form_errors
from ..models import Asset, AssetAssignment, Employee, record_movement
from ..roles import is_ga


@role_required(is_ga)
def assignment_list(request):
    assignments = AssetAssignment.objects.filter(
        status='active'
    ).select_related(
        'asset', 'asset__lokasi_asset', 'assigned_to__department', 'assigned_by'
    ).order_by('-assigned_date')

    unassigned_assets = Asset.objects.filter(
        lifecycle_status='active'
    ).exclude(
        assignments__status='active'
    ).select_related('asset_category', 'lokasi_asset')

    return render(request, 'ga/assignment_list.html', {
        'assignments':       assignments,
        'unassigned_assets': unassigned_assets,
        'total_assigned':    assignments.count(),
        'total_unassigned':  unassigned_assets.count(),
    })


@role_required(is_ga)
def assignment_create(request, asset_id=None):
    employees      = Employee.objects.filter(user_profile__isnull=False).select_related('department')
    selected_asset = get_object_or_404(Asset, id=asset_id) if asset_id else None

    if request.method == 'POST':
        form = AssignmentForm(request.POST)
        if form.is_valid():
            asset    = form.cleaned_data['asset_id']
            assignee = form.cleaned_data['assigned_to']
            # Movement baru + assignment (pemegang lama otomatis 'transferred') dalam 1 jalur
            record_movement(
                asset, held_by=assignee,
                department=assignee.department or asset.owned_by_department,
                location=asset.lokasi_asset, recorded_by=request.user.employee,
                notes=f"Serah terima ke {assignee.nama}",
                tanggal=form.cleaned_data['assigned_date'] or date.today(),
                holder_notes=form.cleaned_data['notes'],
            )
            messages.success(request, f"✅ Aset '{asset.nama_asset}' berhasil diserahkan ke {assignee.nama}.")
            return redirect('assignment_list')
        flash_form_errors(request, form)

    return render(request, 'ga/assignment_create.html', {
        # lokasi & grup di-join — dulu 950+ query karena lazy load per aset
        'assets': Asset.objects.filter(lifecycle_status='active').select_related(
            # asset_category__group: AssetCategory.__str__ memakai nama grup
            'asset_category__group', 'lokasi_asset',
        ),
        'employees':      employees,
        'selected_asset': selected_asset,
        'posted':         request.POST.dict() if request.method == 'POST' else None,
    })


@role_required(is_ga)
def assignment_return(request, pk):
    assignment = get_object_or_404(
        AssetAssignment.objects.select_related('asset', 'assigned_to'), pk=pk, status='active'
    )
    if request.method == 'POST':
        # Assignment ditutup ('returned') oleh record_movement → sync_holder
        asset = assignment.asset
        record_movement(
            asset, held_by=None, department=asset.owned_by_department,
            location=asset.lokasi_asset, recorded_by=request.user.employee,
            notes=f"Dikembalikan oleh {assignment.assigned_to.nama}",
            holder_notes=request.POST.get('return_notes', '').strip(),
        )
        messages.success(
            request,
            f"Aset '{assignment.asset.nama_asset}' berhasil dikembalikan oleh {assignment.assigned_to.nama}."
        )
    return redirect('assignment_list')
