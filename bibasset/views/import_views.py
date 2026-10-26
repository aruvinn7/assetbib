# views/import_views.py
# Fitur import data aset & finansial dari file Excel (.xlsx)

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

import openpyxl
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render

from ..decorators import role_required
from ..models import (
    Asset, AssetCategory, AssetFinancial, AssetGroup, AssetWriteOff,
    Department, Location, MaintenanceSchedule,
)
from ..roles import is_ga
from .utils import is_ga_or_finance


# ================================================================
# KONSTANTA
# ================================================================

ASSET_REQUIRED_COLS  = {'no_po', 'asset_number', 'nama_asset', 'tanggal_pembelian'}
FIN_REQUIRED_COLS    = {'asset_number', 'nilai_awal_aset', 'nilai_residual', 'umur_ekonomis'}
VALID_STATUS         = {'active', 'maintenance', 'writeoff'}
MAX_ROWS             = 500


# ================================================================
# HELPER: baca nilai sel Excel — selalu string bersih
# ================================================================

def _cell_str(val) -> str:
    """Ambil nilai sel, konversi ke string bersih. None/kosong → ''"""
    if val is None:
        return ''
    return str(val).strip()


def _parse_date(val):
    """
    Terima berbagai format tanggal dari Excel.
    Return: date object, atau None kalau gagal.
    """
    if val is None:
        return None
    # Excel terkadang langsung kirim datetime object
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    # Coba parse string
    s = str(val).strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_decimal(val):
    """
    Parse nilai Desimal dari Excel.
    Return: Decimal, atau None kalau gagal.
    """
    if val is None or val == '':
        return None
    # Sel angka Excel datang sebagai int/float — jangan dibuang titik desimalnya
    # (dulu 15000000.0 → "150000000", jadi 10x lipat)
    if isinstance(val, (int, float, Decimal)) and not isinstance(val, bool):
        return Decimal(str(val))
    try:
        s = str(val).strip()
        s = re.sub(r'(?i)rp|\s', '', s)
        if ',' in s and '.' in s:
            # Format Indonesia: 15.000.000,50
            s = s.replace('.', '').replace(',', '.')
        elif ',' in s:
            # 15,000,000 (ribuan) atau 1500,5 (desimal)
            s = s.replace(',', '') if re.fullmatch(r'\d{1,3}(,\d{3})+', s) else s.replace(',', '.')
        elif re.fullmatch(r'\d{1,3}(\.\d{3})+', s):
            # 15.000.000 → titik sebagai pemisah ribuan
            s = s.replace('.', '')
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None



# ================================================================
# FUZZY MATCHING — Toleransi typo pada nama lokasi/dept/group/category
# ================================================================

def _fuzzy_resolve(query: str, lookup: dict, field_name: str):
    """
    Cari nama yang paling mirip di lookup dict.
    Return: (object_or_None, warning_msg_or_None)

    Threshold:
    - >= 0.85 → langsung dipasangkan, catat sebagai auto-fix
    - >= 0.60 → dipasangkan tapi beri peringatan "mungkin maksud kamu X?"
    - <  0.60 → tidak dipasangkan, field jadi NULL
    """
    if not query:
        return None, None

    query_lower = query.lower().strip()

    # Exact match dulu
    if query_lower in lookup:
        return lookup[query_lower], None

    # Fuzzy search
    best_score  = 0
    best_key    = None
    for key in lookup:
        score = SequenceMatcher(None, query_lower, key).ratio()
        if score > best_score:
            best_score = score
            best_key   = key

    if best_key is None:
        return None, None

    best_obj  = lookup[best_key]
    best_name = best_obj.name  # semua model punya .name

    if best_score >= 0.85:
        # Sangat mirip — auto-fix, catat saja sebagai informasi
        warn = f'[Auto-fix] {field_name}: "{query}" → dikenali sebagai "{best_name}"'
        return best_obj, warn

    elif best_score >= 0.60:
        # Agak mirip — pasangkan tapi beri peringatan
        warn = f'[Peringatan] {field_name}: "{query}" tidak ditemukan persis, dipasangkan ke "{best_name}" (skor {best_score:.0%}). Periksa apakah sudah benar.'
        return best_obj, warn

    else:
        # Terlalu berbeda — jangan tebak, biarkan NULL
        return None, f'[Info] {field_name}: "{query}" tidak ditemukan di sistem, field dikosongkan.'


def _read_excel_rows(file_obj, required_cols: set):
    """
    Baca file Excel, return (header_map, rows, error_msg).
    - header_map: {nama_kolom: index_kolom (0-based)}
    - rows: list of dict per baris data
    - error_msg: string kalau ada error fatal
    """
    try:
        wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    except Exception as e:
        return None, None, f"File tidak bisa dibuka: {e}"

    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)

    # Cari baris header — cari baris pertama yang mengandung semua kolom wajib
    header_map = {}
    header_row_found = False
    raw_rows = []

    for raw_row in rows_iter:
        # Normalisasi: hilangkan tanda * dan strip whitespace
        normalized = [
            _cell_str(c).replace('*', '').strip().lower()
            for c in raw_row
        ]
        # Cek apakah ini baris header
        if required_cols.issubset(set(normalized)):
            for idx, col_name in enumerate(normalized):
                if col_name:
                    header_map[col_name] = idx
            header_row_found = True
            break
        raw_rows.append(raw_row)  # baris sebelum header (biasanya title, rules, contoh)

    if not header_row_found:
        missing = required_cols - set(header_map.keys())
        return None, None, (
            f"Kolom wajib tidak ditemukan di file: {', '.join(missing)}. "
            f"Pastikan menggunakan template yang benar."
        )

    # Baca baris data setelah header
    data_rows = []
    for raw_row in rows_iter:
        # Skip baris completely kosong
        if all(c is None or str(c).strip() == '' for c in raw_row):
            continue
        row_dict = {}
        for col_name, idx in header_map.items():
            row_dict[col_name] = raw_row[idx] if idx < len(raw_row) else None
        data_rows.append(row_dict)

    wb.close()
    return header_map, data_rows, None


# ================================================================
# IMPORT ASET
# ================================================================
@role_required(is_ga)
def import_asset(request):
    if request.method != 'POST':
        return render(request, 'asset/import_asset.html')

    file_obj = request.FILES.get('excel_file')
    if not file_obj:
        messages.error(request, "Pilih file Excel terlebih dahulu.")
        return redirect('import_asset')

    if not file_obj.name.endswith(('.xlsx', '.xlsm')):
        messages.error(request, "File harus berformat .xlsx atau .xlsm")
        return redirect('import_asset')

    header_map, rows, err = _read_excel_rows(file_obj, ASSET_REQUIRED_COLS)
    if err:
        messages.error(request, err)
        return redirect('import_asset')

    if len(rows) > MAX_ROWS:
        messages.error(request, f"Maksimal {MAX_ROWS} baris per upload. File ini punya {len(rows)} baris.")
        return redirect('import_asset')

    # Cache master data
    locations   = {loc.name.lower(): loc   for loc in Location.objects.all()}
    departments = {dep.name.lower(): dep   for dep in Department.objects.all()}
    groups      = {grp.name.lower(): grp   for grp in AssetGroup.objects.all()}
    # Kategori dicari di dalam grupnya dulu — nama kategori bisa sama di grup berbeda
    categories_by_group = {}
    categories_any      = {}
    for cat in AssetCategory.objects.all():
        categories_by_group.setdefault(cat.group_id, {})[cat.name.lower()] = cat
        categories_any.setdefault(cat.name.lower(), cat)

    existing_numbers = set(Asset.objects.values_list('asset_number', flat=True))
    created_by = request.user.employee

    assets_to_create = []

    # ── result_rows: semua baris untuk ditampilkan di template ──
    # Setiap item adalah dict dengan:
    #   status: 'success' | 'rejected'
    #   reject_reason: string (kalau rejected)
    #   warnings: list string (fuzzy fix notes)
    #   kolom-kolom data: no_po, asset_number, nama_asset, ...
    result_rows = []

    for row_num, row in enumerate(rows, start=1):
        no_po             = _cell_str(row.get('no_po'))
        asset_number      = _cell_str(row.get('asset_number'))
        nama_asset        = _cell_str(row.get('nama_asset'))
        tanggal_pembelian = _parse_date(row.get('tanggal_pembelian'))

        # Data mentah dari Excel untuk ditampilkan (sebelum resolve)
        raw_lokasi    = _cell_str(row.get('lokasi_asset'))
        raw_dept      = _cell_str(row.get('department'))
        raw_group     = _cell_str(row.get('asset_group'))
        raw_category  = _cell_str(row.get('asset_category'))
        status_raw    = _cell_str(row.get('lifecycle_status')).lower()
        lifecycle     = status_raw if status_raw in VALID_STATUS else 'active'

        # Base row data untuk template
        row_data = {
            'row_num':          row_num,
            'no_po':            no_po,
            'asset_number':     asset_number,
            'nama_asset':       nama_asset,
            'tanggal_pembelian': tanggal_pembelian,
            'lokasi_asset':     raw_lokasi,
            'department':       raw_dept,
            'asset_group':      raw_group,
            'asset_category':   raw_category,
            'lifecycle_status': lifecycle,
            'warnings':         [],
            'status':           'success',
            'reject_reason':    '',
        }

        # ── Validasi kolom wajib ──
        if not all([no_po, asset_number, nama_asset]):
            row_data['status'] = 'rejected'
            row_data['reject_reason'] = "Kolom wajib (no_po / asset_number / nama_asset) kosong"
            result_rows.append(row_data)
            continue

        if tanggal_pembelian is None:
            row_data['status'] = 'rejected'
            row_data['reject_reason'] = f"Format tanggal tidak valid: '{row.get('tanggal_pembelian')}'"
            result_rows.append(row_data)
            continue

        if asset_number in existing_numbers:
            row_data['status'] = 'rejected'
            row_data['reject_reason'] = f"asset_number '{asset_number}' sudah ada di sistem"
            result_rows.append(row_data)
            continue

        # ── Resolve relasi dengan fuzzy matching ──
        row_warnings = []

        def _resolve(field_key_raw, raw_val, lookup, field_label):
            if not raw_val:
                return None
            obj, warn = _fuzzy_resolve(raw_val, lookup, field_label)
            if warn:
                row_warnings.append(warn)
            return obj

        lokasi   = _resolve('lokasi_asset',   raw_lokasi,   locations,   'lokasi_asset')
        dept     = _resolve('department',     raw_dept,     departments, 'department')
        group    = _resolve('asset_group',    raw_group,    groups,      'asset_group')
        category = _resolve(
            'asset_category', raw_category,
            categories_by_group.get(group.pk, {}) if group else categories_any,
            'asset_category',
        )
        if group is None and category is not None:
            group = category.group

        # Update nama yang sudah di-resolve ke row_data (untuk tampilkan hasil fuzzy fix)
        row_data['lokasi_asset']    = lokasi.name   if lokasi   else raw_lokasi
        row_data['department']      = dept.name     if dept     else raw_dept
        row_data['asset_group']     = group.name    if group    else raw_group
        row_data['asset_category']  = category.name if category else raw_category
        row_data['warnings']        = row_warnings

        # ── Kumpulkan aset, disimpan sekaligus setelah loop ──
        asset = Asset(
            no_po=no_po,
            asset_number=asset_number,
            nama_asset=nama_asset,
            tanggal_pembelian=tanggal_pembelian,
            lokasi_asset=lokasi,
            owned_by_department=dept,
            asset_group=group,
            asset_category=category,
            lifecycle_status=lifecycle,
            created_by=created_by,
        )
        existing_numbers.add(asset_number)
        assets_to_create.append(asset)
        result_rows.append(row_data)

    # 1 transaksi: semua aset + jadwal awal masuk, atau tidak sama sekali
    with transaction.atomic():
        created = Asset.objects.bulk_create(assets_to_create, batch_size=200)
        # Sama dengan initialize_schedule_for_asset(): aset baru belum punya
        # custom config, jadi interval = default kategori
        schedules = []
        for asset in created:
            interval = asset.asset_category.default_interval_in_days() if asset.asset_category else None
            if interval and asset.lifecycle_status != 'writeoff':
                schedules.append(MaintenanceSchedule(
                    asset=asset,
                    scheduled_date=asset.tanggal_pembelian + timedelta(days=int(interval)),
                    status='upcoming',
                ))
        MaintenanceSchedule.objects.bulk_create(schedules, batch_size=200)
        # Aset yang diimport langsung berstatus write off tetap butuh catatan write off
        AssetWriteOff.objects.bulk_create([
            AssetWriteOff(asset=a, tanggal=date.today(), alasan='lainnya', created_by=created_by,
                          keterangan='Diimport dari Excel dengan status write off. Lengkapi alasan bila perlu.')
            for a in created if a.lifecycle_status == 'writeoff'
        ])
    success_count = len(created)

    rejected_count = sum(1 for r in result_rows if r['status'] == 'rejected')
    warn_count     = sum(1 for r in result_rows if r['status'] != 'rejected' and r['warnings'])

    return render(request, 'asset/import_result.html', {
        'success_count':  success_count,
        'rejected_count': rejected_count,
        'warn_count':     warn_count,
        'result_rows':    result_rows,
        'import_type':    'Aset',
        'back_url':       'import_asset',
        'list_url':       'asset_list',
    })


# ================================================================
# IMPORT FINANSIAL
# ================================================================

@role_required(is_ga_or_finance)
def import_finansial(request):
    if request.method != 'POST':
        return render(request, 'asset/import_finansial.html')

    file_obj = request.FILES.get('excel_file')
    if not file_obj:
        messages.error(request, "Pilih file Excel terlebih dahulu.")
        return redirect('import_finansial')

    if not file_obj.name.endswith(('.xlsx', '.xlsm')):
        messages.error(request, "File harus berformat .xlsx atau .xlsm")
        return redirect('import_finansial')

    header_map, rows, err = _read_excel_rows(file_obj, FIN_REQUIRED_COLS)
    if err:
        messages.error(request, err)
        return redirect('import_finansial')

    if len(rows) > MAX_ROWS:
        messages.error(request, f"Maksimal {MAX_ROWS} baris per upload.")
        return redirect('import_finansial')

    # Cache: asset_number → Asset object
    assets_map = {
        a.asset_number: a
        for a in Asset.objects.filter(financial__isnull=True)
        # Hanya aset yang BELUM punya finansial
    }

    all_numbers   = set(Asset.objects.values_list('asset_number', flat=True))
    created_by    = request.user.employee
    success_count = 0
    skipped_rows  = []
    financials    = []  # Kumpulkan untuk bulk_create

    for row_num, row in enumerate(rows, start=1):
        asset_number = _cell_str(row.get('asset_number'))
        nilai_awal   = _parse_decimal(row.get('nilai_awal_aset'))
        nilai_resid  = _parse_decimal(row.get('nilai_residual'))
        umur_raw     = row.get('umur_ekonomis')

        # Validasi kolom wajib
        if not asset_number:
            skipped_rows.append((row_num, "asset_number kosong"))
            continue

        if nilai_awal is None:
            skipped_rows.append((row_num, f"nilai_awal_aset tidak valid: '{row.get('nilai_awal_aset')}'. Isi angka saja tanpa titik/koma"))
            continue

        if nilai_resid is None:
            skipped_rows.append((row_num, f"nilai_residual tidak valid: '{row.get('nilai_residual')}'. Isi angka saja"))
            continue

        # Validasi umur_ekonomis
        try:
            umur = int(float(str(umur_raw).strip()))
            if not (1 <= umur <= 100):
                raise ValueError
        except (ValueError, TypeError):
            skipped_rows.append((row_num, f"umur_ekonomis tidak valid: '{umur_raw}'. Isi angka tahun (1-100)"))
            continue

        # Cek asset_number ada di sistem
        asset = assets_map.get(asset_number)
        if asset is None:
            # Cek apakah karena belum ada atau karena sudah punya finansial
            if asset_number in all_numbers:
                skipped_rows.append((row_num, f"Aset '{asset_number}' sudah memiliki data finansial"))
            else:
                skipped_rows.append((row_num, f"Aset '{asset_number}' tidak ditemukan di sistem"))
            continue

        # Validasi nilai residual tidak lebih besar dari nilai awal
        if nilai_resid > nilai_awal:
            skipped_rows.append((row_num, f"nilai_residual ({nilai_resid:,.0f}) tidak boleh lebih besar dari nilai_awal_aset ({nilai_awal:,.0f})"))
            continue

        # pop: asset_number dobel di file yang sama tidak bikin IntegrityError
        assets_map.pop(asset_number)
        financials.append(AssetFinancial(
            asset=asset,
            nilai_awal_aset=nilai_awal,
            nilai_residual=nilai_resid,
            umur_ekonomis=umur,
            created_by=created_by,
        ))
        success_count += 1

    # Bulk create — 1 INSERT untuk semua finansial
    if financials:
        with transaction.atomic():
            AssetFinancial.objects.bulk_create(financials, batch_size=200)

    _add_import_messages(request, success_count, skipped_rows, 'data finansial')

    return render(request, 'asset/import_result.html', {
        'success_count': success_count,
        'skipped_rows':  skipped_rows,
        'import_type':   'Finansial',
        'back_url':      'import_finansial',
        'list_url':      'asset_list',
    })


# ================================================================
# HELPER
# ================================================================

def _add_import_messages(request, success_count, skipped_rows, label):
    if success_count > 0:
        messages.success(request, f"✅ {success_count} {label} berhasil diimport.")
    if skipped_rows:
        messages.warning(request, f"⚠️ {len(skipped_rows)} baris dilewati. Lihat detail di bawah.")
    if success_count == 0 and not skipped_rows:
        messages.error(request, "Tidak ada data yang diproses. Pastikan file tidak kosong.")


# ================================================================
# DOWNLOAD TEMPLATE — GENERATE ON-THE-FLY (tidak butuh file statis)
# User tinggal klik tombol → langsung download .xlsx
# ================================================================

import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from django.http import HttpResponse


def _make_border():
    t = Side(style='thin', color='D1D5DB')
    return Border(top=t, bottom=t, left=t, right=t)

def _header_cell(cell, text, bg='1A56DB', fg='FFFFFF'):
    cell.value = text
    cell.font      = Font(name='Arial', bold=True, color=fg, size=10)
    cell.fill      = PatternFill('solid', start_color=bg)
    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    cell.border    = _make_border()

def _rule_cell(cell, text):
    cell.value     = text
    cell.font      = Font(name='Arial', size=9, italic=True, color='78350F')
    cell.fill      = PatternFill('solid', start_color='FFF8E1')
    cell.alignment = Alignment(wrap_text=True, vertical='top')
    cell.border    = _make_border()

def _example_cell(cell, val):
    cell.value     = val
    cell.font      = Font(name='Arial', size=9, color='1E40AF')
    cell.fill      = PatternFill('solid', start_color='EFF6FF')
    cell.alignment = Alignment(vertical='center')
    cell.border    = _make_border()

def _data_cell(cell):
    cell.font      = Font(name='Arial', size=10)
    cell.alignment = Alignment(vertical='center')
    cell.border    = _make_border()

def _excel_response(wb, filename):
    """Simpan workbook ke buffer, kirim sebagai HTTP response download."""
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    response = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@role_required(is_ga)
def download_template_asset(request):
    """Generate template import aset secara langsung — tidak butuh file statis."""
    wb = Workbook()
    ws = wb.active
    ws.title = "DATA IMPORT"

    # ── Baris 1: Judul ──────────────────────────────────────────
    ws.merge_cells('A1:I1')
    t = ws['A1']
    t.value     = "📦  TEMPLATE IMPORT DATA ASET  |  AssetMS"
    t.font      = Font(name='Arial', bold=True, size=13, color='FFFFFF')
    t.fill      = PatternFill('solid', start_color='1A56DB')
    t.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 32

    ws.merge_cells('A2:I2')
    sub = ws['A2']
    sub.value     = "Hapus baris contoh (baris 6-8, highlight biru) sebelum upload. Baca sheet RULES sebelum mengisi."
    sub.font      = Font(name='Arial', size=9, italic=True, color='6B7280')
    sub.fill      = PatternFill('solid', start_color='F8F9FC')
    sub.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[2].height = 18
    ws.row_dimensions[3].height = 6  # spacer

    # ── Baris 4: Header kolom ───────────────────────────────────
    COLS = [
        ("no_po *",              16, '0E3FBA'),  # biru tua = wajib
        ("asset_number *",       18, '0E3FBA'),
        ("nama_asset *",         30, '0E3FBA'),
        ("tanggal_pembelian *",  20, '0E3FBA'),
        ("lokasi_asset",         25, '1A56DB'),  # biru biasa = opsional
        ("department",           22, '1A56DB'),
        ("asset_group",          18, '1A56DB'),
        ("asset_category",       18, '1A56DB'),
        ("lifecycle_status",     16, '1A56DB'),
    ]
    for col_idx, (name, width, bg) in enumerate(COLS, 1):
        _header_cell(ws.cell(row=4, column=col_idx), name, bg=bg)
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[4].height = 36

    # ── Baris 5: Rules ──────────────────────────────────────────
    RULES = [
        "Nomor PO pembelian.\nContoh: PO-2024-001",
        "UNIK di seluruh sistem.\nContoh: ELK-AC-001",
        "Nama lengkap aset.\nMaks 200 karakter.",
        "Format: YYYY-MM-DD\nContoh: 2024-02-20",
        "Nama lokasi persis\nseperti di sistem.\nKosongkan jika tidak tahu.",
        "Nama departemen\npersis seperti di sistem.",
        "Nama asset group\npersis seperti di sistem.",
        "Nama kategori\npersis seperti di sistem.",
        "active / maintenance\n/ writeoff\nDefault: active",
    ]
    for col_idx, rule in enumerate(RULES, 1):
        _rule_cell(ws.cell(row=5, column=col_idx), rule)
    ws.row_dimensions[5].height = 56

    # ── Baris 6-8: Contoh data ──────────────────────────────────
    EXAMPLES = [
        ["PO-2024-001", "ELK-AC-001", "AC Split Daikin 2 PK BOD Room",    "2024-02-20", "Office Angsana > Lantai 2 > BOD Room", "HRGA",                  "Elektronik",  "AC & Pendingin", "active"],
        ["PO-2024-002", "ELK-LP-001", "Laptop Lenovo ThinkPad E14",        "2024-03-15", "Office Angsana > Lantai 1",            "Digital & Technology",  "IT Equipment","Laptop",         "active"],
        ["PO-2023-089", "FRN-CH-001", "Kursi Ergonomis HighBack",          "2023-11-01", "",                                     "Finance",               "Furnitur",    "Kursi",           "active"],
    ]
    for row_idx, ex in enumerate(EXAMPLES, 6):
        for col_idx, val in enumerate(ex, 1):
            _example_cell(ws.cell(row=row_idx, column=col_idx), val)
        ws.row_dimensions[row_idx].height = 20

    # ── Baris 9-108: Baris kosong untuk diisi ───────────────────
    for row_idx in range(9, 109):
        for col_idx in range(1, len(COLS) + 1):
            _data_cell(ws.cell(row=row_idx, column=col_idx))
        ws.row_dimensions[row_idx].height = 20

    ws.freeze_panes = 'A6'  # freeze sampai baris rules

    # ── Sheet 2: Rules lengkap ───────────────────────────────────
    ws2 = wb.create_sheet("RULES & PANDUAN")
    ws2.column_dimensions['A'].width = 22
    ws2.column_dimensions['B'].width = 40
    ws2.column_dimensions['C'].width = 12
    ws2.column_dimensions['D'].width = 28
    ws2.column_dimensions['E'].width = 30

    ws2.merge_cells('A1:E1')
    r = ws2['A1']
    r.value     = "📋  RULES & PANDUAN — IMPORT ASET"
    r.font      = Font(name='Arial', bold=True, size=13, color='FFFFFF')
    r.fill      = PatternFill('solid', start_color='1A56DB')
    r.alignment = Alignment(horizontal='center', vertical='center')
    ws2.row_dimensions[1].height = 32

    for col_idx, h in enumerate(['KOLOM', 'DESKRIPSI', 'STATUS', 'FORMAT', 'CONTOH'], 1):
        c = ws2.cell(row=3, column=col_idx)
        _header_cell(c, h, bg='374151')
    ws2.row_dimensions[3].height = 24

    RULES_DETAIL = [
        ("no_po",            "Nomor Purchase Order pembelian aset",                        "WAJIB",   "Teks bebas",                                  "PO-2024-001"),
        ("asset_number",     "Kode unik aset. TIDAK BOLEH duplikat di sistem",             "WAJIB",   "Teks bebas, unik",                            "ELK-AC-001"),
        ("nama_asset",       "Nama lengkap dan deskriptif aset",                           "WAJIB",   "Teks bebas, maks 200 karakter",               "AC Split Daikin 2 PK"),
        ("tanggal_pembelian","Tanggal aset dibeli / diterima",                             "WAJIB",   "YYYY-MM-DD saja\nContoh: 2024-02-20",         "2024-02-20"),
        ("lokasi_asset",     "Nama lokasi. Harus persis sama dengan data di sistem",       "Opsional","Teks. Kosongkan jika tidak ada",               "Office Angsana > Lantai 2"),
        ("department",       "Nama departemen pemilik aset",                               "Opsional","Teks. Harus persis sama di sistem",            "HRGA"),
        ("asset_group",      "Grup aset (Elektronik, Furnitur, dll)",                      "Opsional","Teks. Harus persis sama di sistem",            "Elektronik"),
        ("asset_category",   "Kategori spesifik dalam grup",                               "Opsional","Teks. Harus persis sama di sistem",            "AC & Pendingin"),
        ("lifecycle_status", "Status awal aset saat diimport",                             "Opsional","active / maintenance / writeoff\nDefault: active","active"),
    ]
    for row_idx, (col, desc, req, fmt, ex) in enumerate(RULES_DETAIL, 4):
        data = [col, desc, req, fmt, ex]
        bgs  = ['EFF6FF', 'FFFFFF', 'FFF8E1' if req == 'WAJIB' else 'F0FDF4', 'FFFFFF', 'EFF6FF']
        for col_idx, (val, bg) in enumerate(zip(data, bgs), 1):
            c = ws2.cell(row=row_idx, column=col_idx, value=val)
            c.font      = Font(name='Arial', size=9, bold=(col_idx == 1))
            c.fill      = PatternFill('solid', start_color=bg)
            c.alignment = Alignment(vertical='center', wrap_text=True)
            c.border    = _make_border()
        ws2.row_dimensions[row_idx].height = 38

    # Catatan penting
    ws2.merge_cells('A14:E14')
    note_title = ws2['A14']
    note_title.value     = "⚠️  CATATAN PENTING"
    note_title.font      = Font(name='Arial', bold=True, size=11, color='FFFFFF')
    note_title.fill      = PatternFill('solid', start_color='D97706')
    note_title.alignment = Alignment(horizontal='left', vertical='center', indent=1)
    ws2.row_dimensions[14].height = 26

    NOTES = [
        ("Kolom WAJIB",        "no_po, asset_number, nama_asset, tanggal_pembelian harus diisi semua. Baris yang kolom wajibnya kosong akan DILEWATI."),
        ("asset_number unik",  "Jika asset_number sudah ada di sistem, baris DILEWATI — tidak update, tidak error."),
        ("Lokasi & Dept",      "Jika nama tidak ditemukan di sistem, field dikosongkan. Aset tetap tersimpan."),
        ("Format tanggal",     "HANYA YYYY-MM-DD. Format lain (20/02/2024 dll) menyebabkan baris DILEWATI."),
        ("Hapus baris contoh", "Hapus baris contoh (highlight biru, baris 6-8) sebelum upload."),
        ("Maks upload",        "Maksimal 500 baris per sekali upload."),
    ]
    for row_idx, (title_n, note) in enumerate(NOTES, 15):
        c1 = ws2.cell(row=row_idx, column=1, value=f"🔸 {title_n}")
        c1.font      = Font(name='Arial', bold=True, size=9, color='92400E')
        c1.fill      = PatternFill('solid', start_color='FFF8E1')
        c1.alignment = Alignment(vertical='center', wrap_text=True)
        c1.border    = _make_border()
        ws2.merge_cells(f'B{row_idx}:E{row_idx}')
        c2 = ws2.cell(row=row_idx, column=2, value=note)
        c2.font      = Font(name='Arial', size=9)
        c2.fill      = PatternFill('solid', start_color='FFFBEB')
        c2.alignment = Alignment(vertical='center', wrap_text=True)
        c2.border    = _make_border()
        ws2.row_dimensions[row_idx].height = 34

    return _excel_response(wb, 'template_import_asset.xlsx')


@role_required(is_ga_or_finance)
def download_template_finansial(request):
    """Generate template import finansial secara langsung."""
    wb = Workbook()
    ws = wb.active
    ws.title = "DATA IMPORT"

    ws.merge_cells('A1:D1')
    t = ws['A1']
    t.value     = "💰  TEMPLATE IMPORT DATA FINANSIAL  |  AssetMS"
    t.font      = Font(name='Arial', bold=True, size=13, color='FFFFFF')
    t.fill      = PatternFill('solid', start_color='057A55')
    t.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 32

    ws.merge_cells('A2:D2')
    sub = ws['A2']
    sub.value     = "Import SETELAH import aset. asset_number harus sudah ada di sistem."
    sub.font      = Font(name='Arial', size=9, italic=True, color='6B7280')
    sub.fill      = PatternFill('solid', start_color='F8F9FC')
    sub.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[2].height = 18
    ws.row_dimensions[3].height = 6

    FIN_COLS = [
        ("asset_number *",    22, '065F46'),
        ("nilai_awal_aset *", 22, '065F46'),
        ("nilai_residual *",  22, '065F46'),
        ("umur_ekonomis *",   18, '065F46'),
    ]
    for col_idx, (name, width, bg) in enumerate(FIN_COLS, 1):
        _header_cell(ws.cell(row=4, column=col_idx), name, bg=bg)
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[4].height = 36

    FIN_RULES = [
        "Kode aset yang sudah\nada di sistem.\nHarus sama persis.",
        "Harga beli (Rupiah).\nAngka saja, tanpa\ntitik/koma.\nContoh: 15000000",
        "Nilai sisa di akhir\numur ekonomis.\nBoleh 0. Harus ≤\nnilai_awal_aset.",
        "Umur ekonomis\ndalam TAHUN.\nAngka bulat 1-100.\nContoh: 5",
    ]
    for col_idx, rule in enumerate(FIN_RULES, 1):
        _rule_cell(ws.cell(row=5, column=col_idx), rule)
    ws.row_dimensions[5].height = 70

    FIN_EXAMPLES = [
        ["ELK-AC-001", 15000000, 1500000, 5],
        ["ELK-LP-001", 12000000, 1200000, 4],
        ["FRN-CH-001",  3500000,  350000, 8],
    ]
    for row_idx, ex in enumerate(FIN_EXAMPLES, 6):
        for col_idx, val in enumerate(ex, 1):
            c = ws.cell(row=row_idx, column=col_idx)
            _example_cell(c, val)
            if col_idx in [2, 3]:
                c.number_format = '#,##0'
        ws.row_dimensions[row_idx].height = 20

    for row_idx in range(9, 109):
        for col_idx in range(1, 5):
            c = ws.cell(row=row_idx, column=col_idx)
            _data_cell(c)
            if col_idx in [2, 3]:
                c.number_format = '#,##0'
        ws.row_dimensions[row_idx].height = 20

    ws.freeze_panes = 'A6'

    # Sheet rules
    ws2 = wb.create_sheet("RULES & PANDUAN")
    ws2.column_dimensions['A'].width = 22
    ws2.column_dimensions['B'].width = 45
    ws2.column_dimensions['C'].width = 12
    ws2.column_dimensions['D'].width = 30
    ws2.column_dimensions['E'].width = 25

    ws2.merge_cells('A1:E1')
    r = ws2['A1']
    r.value     = "📋  RULES & PANDUAN — IMPORT FINANSIAL"
    r.font      = Font(name='Arial', bold=True, size=13, color='FFFFFF')
    r.fill      = PatternFill('solid', start_color='057A55')
    r.alignment = Alignment(horizontal='center', vertical='center')
    ws2.row_dimensions[1].height = 32

    ws2.merge_cells('A3:E3')
    info = ws2['A3']
    info.value     = "⚠️ Import finansial dilakukan SETELAH import aset. asset_number harus sudah ada di database."
    info.font      = Font(name='Arial', bold=True, size=10, color='065F46')
    info.fill      = PatternFill('solid', start_color='D1FAE5')
    info.alignment = Alignment(horizontal='center', vertical='center')
    ws2.row_dimensions[3].height = 26

    for col_idx, h in enumerate(['KOLOM', 'DESKRIPSI', 'STATUS', 'FORMAT', 'CONTOH'], 1):
        c = ws2.cell(row=5, column=col_idx)
        _header_cell(c, h, bg='374151')
    ws2.row_dimensions[5].height = 24

    FIN_RULES_DETAIL = [
        ("asset_number",    "Kode unik aset. HARUS sudah ada di database aset",                "WAJIB", "Teks. Persis sama dengan di sistem",          "ELK-AC-001"),
        ("nilai_awal_aset", "Harga perolehan/beli aset dalam Rupiah",                          "WAJIB", "Angka bulat tanpa Rp/titik/koma",             "15000000"),
        ("nilai_residual",  "Nilai sisa aset di akhir umur ekonomis. Bisa diisi 0",            "WAJIB", "Angka bulat ≥ 0. Harus ≤ nilai_awal_aset",    "1500000"),
        ("umur_ekonomis",   "Perkiraan usia ekonomis dalam tahun",                             "WAJIB", "Angka bulat 1–100",                           "5"),
    ]
    for row_idx, (col, desc, req, fmt, ex) in enumerate(FIN_RULES_DETAIL, 6):
        data = [col, desc, req, fmt, ex]
        bgs  = ['EFF6FF', 'FFFFFF', 'FFF8E1', 'FFFFFF', 'EFF6FF']
        for col_idx, (val, bg) in enumerate(zip(data, bgs), 1):
            c = ws2.cell(row=row_idx, column=col_idx, value=val)
            c.font      = Font(name='Arial', size=9, bold=(col_idx == 1))
            c.fill      = PatternFill('solid', start_color=bg)
            c.alignment = Alignment(vertical='center', wrap_text=True)
            c.border    = _make_border()
        ws2.row_dimensions[row_idx].height = 38

    ws2.merge_cells('A11:E11')
    nt = ws2['A11']
    nt.value     = "⚠️  CATATAN PENTING"
    nt.font      = Font(name='Arial', bold=True, size=11, color='FFFFFF')
    nt.fill      = PatternFill('solid', start_color='D97706')
    nt.alignment = Alignment(horizontal='left', vertical='center', indent=1)
    ws2.row_dimensions[11].height = 26

    FIN_NOTES = [
        ("Urutan import",      "Import ASET dulu, baru FINANSIAL. Finansial tidak bisa diimport untuk aset yang belum ada."),
        ("Duplikat finansial", "Jika aset sudah punya data finansial, baris DILEWATI. Gunakan fitur Edit untuk update."),
        ("nilai_residual",     "Tidak boleh lebih besar dari nilai_awal_aset. Jika lebih besar, baris DILEWATI."),
        ("Format angka",       "Isi ANGKA SAJA. Jangan pakai 'Rp', titik ribuan. Salah: Rp15.000.000 | Benar: 15000000"),
        ("umur_ekonomis",      "Satuan TAHUN. Minimum 1, maksimum 100."),
    ]
    for row_idx, (title_n, note) in enumerate(FIN_NOTES, 12):
        c1 = ws2.cell(row=row_idx, column=1, value=f"🔸 {title_n}")
        c1.font      = Font(name='Arial', bold=True, size=9, color='92400E')
        c1.fill      = PatternFill('solid', start_color='FFF8E1')
        c1.alignment = Alignment(vertical='center', wrap_text=True)
        c1.border    = _make_border()
        ws2.merge_cells(f'B{row_idx}:E{row_idx}')
        c2 = ws2.cell(row=row_idx, column=2, value=note)
        c2.font      = Font(name='Arial', size=9)
        c2.fill      = PatternFill('solid', start_color='FFFBEB')
        c2.alignment = Alignment(vertical='center', wrap_text=True)
        c2.border    = _make_border()
        ws2.row_dimensions[row_idx].height = 34

    return _excel_response(wb, 'template_import_finansial.xlsx')