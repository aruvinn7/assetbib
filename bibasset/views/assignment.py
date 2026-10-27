# views/assignment.py

from django.contrib import messages
from django.db.models import OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..decorators import role_required
from ..forms import AssignmentForm, flash_form_errors
from ..models import Asset, AssetAssignment, Employee, record_movement
from ..roles import is_ga


@role_required(is_ga)
def assignment_list(request):
    search = request.GET.get('q', '').strip()
    assignments = AssetAssignment.objects.filter(
        status='active'
    ).select_related(
        'asset', 'asset__lokasi_asset', 'assigned_to__department', 'assigned_by'
    ).order_by('-assigned_date', '-id')
    if search:
        assignments = assignments.filter(
            Q(asset__nama_asset__icontains=search) | Q(asset__asset_number__icontains=search) |
            Q(assigned_to__nama__icontains=search) | Q(assigned_to__department__name__icontains=search)
        )

    unassigned_assets = Asset.objects.filter(
        lifecycle_status='active'
    ).exclude(
        assignments__status='active'
    ).select_related('lokasi_asset').order_by('-id')

    return render(request, 'ga/assignment_list.html', {
        'assignments':       assignments,
        'search':            search,
        # Panel samping cukup memuat aset terbaru; sisanya dicari di form serah terima
        'unassigned_assets': unassigned_assets[:12],
        'total_assigned':    AssetAssignment.objects.filter(status='active').count(),
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
                tanggal=form.cleaned_data['assigned_date'] or timezone.localdate(),
                holder_notes=form.cleaned_data['notes'],
            )
            messages.success(request, f"Aset '{asset.nama_asset}' diserahkan ke {assignee.nama}.")
            return redirect('assignment_list')
        flash_form_errors(request, form)

    return render(request, 'ga/assignment_create.html', {
        # lokasi & grup di-join — dulu 950+ query karena lazy load per aset
        # Pemegang saat ini ikut dimuat supaya form bisa memberi tahu bila aset akan dipindahtangankan
        'assets': Asset.objects.filter(lifecycle_status='active').select_related(
            'asset_category', 'lokasi_asset',
        ).annotate(holder_name=Subquery(
            AssetAssignment.objects.filter(asset=OuterRef('pk'), status='active').values('assigned_to__nama')[:1]
        )).order_by('nama_asset'),
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
            f"Aset '{assignment.asset.nama_asset}' sudah dikembalikan oleh {assignment.assigned_to.nama}."
        )
    return redirect('assignment_list')
