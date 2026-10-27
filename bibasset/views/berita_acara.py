# views/berita_acara.py
# Berita Acara Kerusakan & Penghapusan Asset.
# Draf terisi otomatis dari temuan kerusakan (lihat models.catat_temuan_kerusakan);
# GA meninjau → terbitkan → cetak & tanda tangan basah → proses penghapusan.
from datetime import date

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..decorators import role_required
from ..forms import MAX_UPLOAD_SIZE
from ..models import (
    Asset, AssetWriteOff, BeritaAcara, BeritaAcaraFoto, BeritaAcaraItem, PenandaTanganBA,
    hapuskan_aset, nomor_ba_berikutnya, terbitkan_ba,
)
from ..roles import is_ga


def _ba(pk):
    return get_object_or_404(BeritaAcara.objects.select_related('department', 'rujukan'), pk=pk)


@role_required(is_ga)
def ba_list(request):
    tab = request.GET.get('tab', 'draf')
    drafts = (BeritaAcara.objects.filter(jenis='kerusakan', status='draft')
              .annotate(n=Count('items'), terakhir=Max('items__created_at'),
                        n_hapus=Count('items', filter=Q(items__rekomendasi='hapus')))
              .filter(n__gt=0).select_related('department').order_by('-terakhir'))
    issued = (BeritaAcara.objects.exclude(status='draft').annotate(n=Count('items'))
              .select_related('department', 'rujukan').order_by('-tanggal', '-urutan'))
    counts = {
        'draf': sum(1 for _ in drafts),
        'kerusakan': issued.filter(jenis='kerusakan').count(),
        'penghapusan': issued.filter(jenis='penghapusan').count(),
        'temuan': sum(d.n for d in drafts),
        'menunggu_ttd': issued.filter(status='terbit').count(),
    }
    if tab in ('kerusakan', 'penghapusan'):
        issued = issued.filter(jenis=tab)
    return render(request, 'berita_acara/list.html', {
        'tab': tab, 'drafts': drafts, 'issued': issued, 'counts': counts,
    })


@role_required(is_ga)
def ba_detail(request, pk):
    ba = _ba(pk)
    if ba.status == 'draft':
        return _draft_editor(request, ba)
    return render(request, 'berita_acara/detail.html', {
        'ba': ba,
        'baris': ba.baris_dokumen(),
        'foto': ba.lampiran_foto(),
        'turunan': ba.turunan.order_by('tanggal'),
        'bisa_hapus': ba.jenis == 'kerusakan' and ba.items.filter(
            rekomendasi='hapus').exclude(asset__lifecycle_status='writeoff').exists(),
        'is_ga': is_ga(request.user),
    })


def _draft_editor(request, ba):
    items = ba.items.select_related('asset', 'asset__lokasi_asset', 'damage_report', 'maintenance_record') \
                    .prefetch_related('foto')
    if request.method == 'POST':
        if not is_ga(request.user):
            return redirect('ba_detail', pk=ba.pk)
        with transaction.atomic():
            for it in items:
                it.kerusakan = request.POST.get(f'kerusakan-{it.pk}', it.kerusakan).strip()
                rek = request.POST.get(f'rekomendasi-{it.pk}')
                if rek in dict(BeritaAcaraItem.REKOMENDASI):
                    it.rekomendasi = rek
                it.save(update_fields=['kerusakan', 'rekomendasi'])
            ba.isi = request.POST.get('isi', '').strip()
            ba.save(update_fields=['isi', 'updated_at'])
        if request.POST.get('aksi') == 'terbitkan':
            try:
                tanggal = date.fromisoformat(request.POST.get('tanggal', ''))
            except ValueError:
                tanggal = None
            kosong = [it.asset.nama_asset for it in items if not it.kerusakan]
            if not tanggal or tanggal > timezone.localdate():
                messages.error(request, 'Isi tanggal verifikasi (tidak boleh di masa depan).', extra_tags='field:tanggal|Tanggal verifikasi')
            elif not items:
                messages.error(request, 'Draf masih kosong.')
            elif kosong:
                messages.error(request, f'Keterangan kerusakan belum diisi untuk: {", ".join(kosong[:3])}.')
            else:
                terbitkan_ba(ba, tanggal, by=request.user.employee)
                messages.success(request, f'Berita acara {ba.nomor} terbit. Cetak lalu mintakan tanda tangan.')
                return redirect('ba_detail', pk=ba.pk)
        else:
            messages.success(request, 'Draf disimpan.')
            return redirect('ba_detail', pk=ba.pk)

    taken = set(items.values_list('asset_id', flat=True))
    kandidat = (Asset.objects.exclude(lifecycle_status='writeoff').exclude(pk__in=taken)
                .select_related('lokasi_asset').order_by('nama_asset'))
    if ba.department_id:
        kandidat = kandidat.filter(owned_by_department_id=ba.department_id)
    tanggal = timezone.localdate()
    return render(request, 'berita_acara/draft.html', {
        'ba': ba,
        'items': items,
        'kandidat': kandidat,
        'today': tanggal,
        'nomor_berikutnya': nomor_ba_berikutnya(tanggal)[1],
        'isi_baku': ba.isi_baku(tanggal),
        'penanda': PenandaTanganBA.objects.all(),
        'is_ga': is_ga(request.user),
        'n_hapus': sum(1 for it in items if it.rekomendasi == 'hapus'),
    })


@role_required(is_ga)
@require_POST
def ba_item_add(request, pk):
    ba = _ba(pk)
    asset = Asset.objects.filter(pk=request.POST.get('asset_id')).exclude(lifecycle_status='writeoff').first()
    kerusakan = request.POST.get('kerusakan', '').strip()
    if ba.status != 'draft' or not asset:
        messages.error(request, 'Pilih aset yang akan ditambahkan.', extra_tags='field:asset_id|Aset')
    elif ba.items.filter(asset=asset).exists():
        messages.warning(request, f'{asset.nama_asset} sudah ada di draf ini.')
    else:
        BeritaAcaraItem.objects.create(ba=ba, asset=asset, sumber='manual', kerusakan=kerusakan)
        messages.success(request, f'{asset.nama_asset} ditambahkan ke draf.')
    return redirect(reverse('ba_detail', args=[ba.pk]) + '#items')


@role_required(is_ga)
@require_POST
def ba_item_delete(request, pk, item_pk):
    item = get_object_or_404(BeritaAcaraItem.objects.select_related('asset'), pk=item_pk, ba_id=pk, ba__status='draft')
    item.delete()
    messages.success(request, f'{item.asset.nama_asset} dikeluarkan dari draf.')
    return redirect(reverse('ba_detail', args=[pk]) + '#items')


@role_required(is_ga)
@require_POST
def ba_item_photo(request, pk, item_pk):
    item = get_object_or_404(BeritaAcaraItem, pk=item_pk, ba_id=pk, ba__status='draft')
    foto = request.FILES.get('foto')
    if not foto:
        messages.error(request, 'Pilih foto dokumentasi.')
    elif foto.size > MAX_UPLOAD_SIZE:
        messages.error(request, 'Ukuran foto maksimal 5 MB.')
    else:
        BeritaAcaraFoto.objects.create(item=item, foto=foto, caption=item.asset.nama_asset)
        messages.success(request, 'Foto dokumentasi ditambahkan.')
    return redirect(reverse('ba_detail', args=[pk]) + f'#item-{item_pk}')


@role_required(is_ga)
@require_POST
def ba_signed(request, pk):
    ba = _ba(pk)
    scan = request.FILES.get('scan_ttd')
    if ba.status != 'terbit':
        return redirect('ba_detail', pk=pk)
    if scan and scan.size > 10 * 1024 * 1024:
        messages.error(request, 'Ukuran file scan maksimal 10 MB.')
        return redirect('ba_detail', pk=pk)
    if scan:
        ba.scan_ttd = scan
    ba.status = 'ditandatangani'
    ba.ditandatangani_at = timezone.now()
    ba.save()
    messages.success(request, f'{ba.nomor} ditandai sudah ditandatangani.')
    return redirect('ba_detail', pk=pk)


@role_required(is_ga)
def ba_print(request, pk):
    ba = _ba(pk)
    return render(request, 'berita_acara/cetak.html', {
        'ba': ba, 'baris': ba.baris_dokumen(), 'foto': ba.lampiran_foto(),
        'penanda': ba.penanda_tangan if ba.status != 'draft' else
                   [{'peran': p.peran, 'nama': p.nama, 'jabatan': p.jabatan} for p in PenandaTanganBA.objects.all()],
    })


@role_required(is_ga)
def ba_penghapusan(request, pk):
    """Write off aset berekomendasi "Dihapuskan" dari BA Kerusakan yang sudah ditandatangani."""
    ba = _ba(pk)
    if ba.jenis != 'kerusakan' or ba.status != 'ditandatangani':
        messages.warning(request, 'Penghapusan hanya dari BA Kerusakan yang sudah ditandatangani.')
        return redirect('ba_detail', pk=pk)
    items = list(ba.items.select_related('asset', 'asset__financial').exclude(asset__lifecycle_status='writeoff'))
    posted = request.POST if request.method == 'POST' else None
    if posted:
        pilih = {int(x) for x in posted.getlist('item') if x.isdigit()}
        assets = [it.asset for it in items if it.pk in pilih]
        try:
            tanggal = date.fromisoformat(posted.get('tanggal', ''))
        except ValueError:
            tanggal = None
        alasan = posted.get('alasan')
        keterangan = posted.get('keterangan', '').strip()
        if not assets:
            messages.error(request, 'Pilih minimal satu aset yang dihapuskan.')
        elif not tanggal or tanggal > timezone.localdate() or tanggal < ba.tanggal:
            messages.error(request, f'Tanggal penghapusan antara {ba.tanggal:%d/%m/%Y} dan hari ini.',
                           extra_tags='field:tanggal|Tanggal penghapusan')
        elif alasan not in dict(AssetWriteOff.REASON):
            messages.error(request, 'Pilih alasan penghapusan.', extra_tags='field:alasan|Alasan')
        elif len(keterangan) < 10:
            messages.error(request, 'Jelaskan dasar penghapusan (minimal 10 karakter).', extra_tags='field:keterangan|Keterangan')
        elif not posted.get('konfirmasi'):
            messages.error(request, 'Centang konfirmasi untuk melanjutkan.', extra_tags='field:konfirmasi|Konfirmasi')
        else:
            baru = hapuskan_aset(assets, tanggal, alasan, keterangan, by=request.user.employee, rujukan=ba)
            messages.success(request, f'{len(assets)} aset di-write off. {baru.nomor} terbit.')
            return redirect('ba_detail', pk=baru.pk)
    return render(request, 'berita_acara/penghapusan.html', {
        'ba': ba, 'items': items, 'today': timezone.localdate(), 'reasons': AssetWriteOff.REASON,
        'pilih': {int(x) for x in posted.getlist('item') if x.isdigit()} if posted else
                 {it.pk for it in items if it.rekomendasi == 'hapus'},
        'cur_alasan': (posted or {}).get('alasan', 'rusak'),
        'keterangan': (posted or {}).get('keterangan', f'Sesuai Berita Acara Kerusakan Asset No: {ba.nomor}.'),
    })


@role_required(is_ga)
def ba_penanda_tangan(request):
    rows = list(PenandaTanganBA.objects.all())
    if request.method == 'POST':
        with transaction.atomic():
            PenandaTanganBA.objects.all().delete()
            n = 0
            for i in range(8):
                peran, nama, jabatan = (request.POST.get(f'{k}-{i}', '').strip() for k in ('peran', 'nama', 'jabatan'))
                if peran and nama:
                    PenandaTanganBA.objects.create(urutan=i, peran=peran, nama=nama, jabatan=jabatan)
                    n += 1
        messages.success(request, f'Lembar pengesahan disimpan ({n} penanda tangan). Berlaku untuk BA yang terbit berikutnya.')
        return redirect('ba_penanda_tangan')
    rows += [None] * (6 - len(rows))
    return render(request, 'berita_acara/penanda_tangan.html', {'rows': rows})
