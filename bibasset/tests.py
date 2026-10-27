from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from .models import (
    Asset, AssetCategory, AssetFinancial, AssetGroup, DamageReport, Employee,
    FinanceProfile, GAProfile, Location, MaintenanceRecord, UserProfile,
    compute_health, get_repair_threshold,
)
from .views.import_views import _parse_decimal


def make_employee(username, profile=None):
    user = User.objects.create_user(username, password='x')
    emp = Employee.objects.create(user=user, employee_id=username, nama=username, jabatan='-')
    if profile:
        profile.objects.create(employee=emp)
    return user, emp


class HealthRulesTest(TestCase):
    def test_threshold_tiers(self):
        # Satu nilai per tier REPAIR_THRESHOLD_TIERS; batas atas tier masuk ke tier berikutnya
        self.assertEqual(get_repair_threshold(1_000_000),
                         {'warning': 15, 'waspada': 30, 'critical': 50})
        self.assertEqual(get_repair_threshold(2_000_000)['warning'], 20)
        self.assertEqual(get_repair_threshold(5_000_000),
                         {'warning': 20, 'waspada': 35, 'critical': 50})
        self.assertEqual(get_repair_threshold(20_000_000),
                         {'warning': 25, 'waspada': 40, 'critical': 60})
        self.assertEqual(get_repair_threshold(100_000_000),
                         {'warning': 30, 'waspada': 50, 'critical': 75})
        self.assertEqual(get_repair_threshold(None)['critical'], 50)

    def test_compute_health(self):
        self.assertEqual(compute_health(0, 0, None)[0], 'no_data')
        self.assertEqual(compute_health(0, 5, None)[0], 'warning')
        self.assertEqual(compute_health(700_000, 0, 1_000_000)[0], 'critical')
        self.assertEqual(compute_health(100_000, 0, 1_000_000)[0], 'normal')
        self.assertEqual(compute_health(100_000, 5, 1_000_000)[0], 'warning')

    def test_sql_critical_filter_matches_python(self):
        for i, biaya in enumerate([700_000, 100_000]):
            a = Asset.objects.create(no_po='PO', asset_number=f'A{i}', nama_asset='x',
                                     tanggal_pembelian=date(2024, 1, 1))
            AssetFinancial.objects.create(asset=a, nilai_awal_aset=1_000_000, nilai_residual=0)
            MaintenanceRecord.objects.create(asset=a, maintenance_type='corrective',
                                             tanggal_pelaksanaan=date(2024, 6, 1),
                                             deskripsi_pekerjaan='-', biaya=biaya)
        critical = Asset.objects.with_health_annotations().critical_health()
        self.assertEqual(list(critical.values_list('asset_number', flat=True)), ['A0'])


class FinancialTest(TestCase):
    def test_book_value_not_below_residual(self):
        a = Asset.objects.create(no_po='PO', asset_number='F1', nama_asset='x',
                                 tanggal_pembelian=date.today() - timedelta(days=365 * 10))
        fin = AssetFinancial.objects.create(asset=a, nilai_awal_aset=10_000_000,
                                            nilai_residual=1_000_000, umur_ekonomis=5)
        self.assertEqual(fin.book_value(), Decimal('1000000'))


class LocationPathTest(TestCase):
    def test_full_path_follows_parent_rename(self):
        root  = Location.objects.create(name='Site')
        child = Location.objects.create(name='Gedung', parent=root)
        self.assertEqual(str(child), 'Site > Gedung')
        root.name = 'Site Baru'
        root.save()
        child.refresh_from_db()
        self.assertEqual(str(child), 'Site Baru > Gedung')


class ImportParseTest(TestCase):
    def test_parse_decimal(self):
        self.assertEqual(_parse_decimal(15000000.0), Decimal('15000000.0'))
        self.assertEqual(_parse_decimal('15.000.000'), Decimal('15000000'))
        self.assertEqual(_parse_decimal('15.000.000,50'), Decimal('15000000.50'))
        self.assertEqual(_parse_decimal('Rp 2.500.000'), Decimal('2500000'))
        self.assertIsNone(_parse_decimal('abc'))


class AccessAndFlowTest(TestCase):
    def setUp(self):
        self.ga_user, self.ga = make_employee('ga', GAProfile)
        self.fin_user, _ = make_employee('fin', FinanceProfile)
        self.usr_user, self.usr = make_employee('usr', UserProfile)
        group = AssetGroup.objects.create(name='AC')
        self.cat = AssetCategory.objects.create(group=group, name='Split', default_interval_value=3)
        self.asset = Asset.objects.create(no_po='PO', asset_number='AC-1', nama_asset='AC',
                                          tanggal_pembelian=date(2025, 1, 1),
                                          asset_category=self.cat)

    def test_role_restrictions(self):
        self.client.force_login(self.usr_user)
        self.assertRedirects(self.client.get('/asset/'), '/', fetch_redirect_response=False)
        self.client.force_login(self.fin_user)
        self.client.get(f'/master/assetgroup/{self.cat.group_id}/delete/')
        self.assertTrue(AssetGroup.objects.filter(pk=self.cat.group_id).exists())

    def test_asset_create_validation(self):
        self.client.force_login(self.ga_user)
        r = self.client.post('/asset/create/', {'no_po': 'PO', 'nama_asset': 'X',
                                                'asset_number': 'AC-1', 'tanggal_pembelian': '2025-01-01'})
        self.assertEqual(r.status_code, 200)            # re-render, bukan 500
        self.assertEqual(Asset.objects.count(), 1)

    def test_damage_report_flow(self):
        self.client.force_login(self.ga_user)
        self.client.post('/ga/assignment/buat/', {'asset_id': self.asset.pk, 'assigned_to': self.usr.pk})
        self.client.force_login(self.usr_user)
        self.client.post('/user/laporan-kerusakan/buat/', {
            'asset_id': self.asset.pk, 'title': 'Mati', 'description': '-', 'severity': 'high'})
        report = DamageReport.objects.get()
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.lifecycle_status, 'maintenance')
        self.assertEqual(report.corrective_schedules.get().status, 'overdue')

        self.client.force_login(self.ga_user)
        self.client.post(f'/ga/laporan-kerusakan/{report.pk}/resolve/', {
            'tanggal_pelaksanaan': '2025-06-01', 'deskripsi_pekerjaan': 'fix', 'hasil': 'ok'})
        report.refresh_from_db(); self.asset.refresh_from_db()
        self.assertEqual(report.report_status, 'resolved')
        self.assertEqual(self.asset.lifecycle_status, 'active')
        self.assertEqual(report.corrective_schedules.get().status, 'done')

    def test_destructive_actions_need_post(self):
        self.client.force_login(self.ga_user)
        sch = self.asset.schedules.create(scheduled_date=date.today(), status='upcoming')
        # Link GET (prefetch browser, tautan yang dibagikan) tidak boleh mengubah data
        self.assertEqual(self.client.get(f'/asset/{self.asset.pk}/delete/').status_code, 405)
        self.assertEqual(self.client.get(f'/maintenance/schedule/{sch.pk}/skip/').status_code, 405)
        self.assertTrue(Asset.objects.filter(pk=self.asset.pk).exists())
        sch.refresh_from_db()
        self.assertEqual(sch.status, 'upcoming')

        self.client.post(f'/maintenance/schedule/{sch.pk}/skip/')
        sch.refresh_from_db()
        self.assertEqual(sch.status, 'skipped')
        self.client.post(f'/asset/{self.asset.pk}/delete/')
        self.assertFalse(Asset.objects.filter(pk=self.asset.pk).exists())

    def test_berita_acara_flow(self):
        from .models import BeritaAcara, MaintenanceRecord as MR, PenandaTanganBA
        # Temuan inspeksi bermasalah → otomatis masuk draf BA Kerusakan
        MR.objects.create(asset=self.asset, maintenance_type='inspection', tanggal_pelaksanaan=date.today(),
                          deskripsi_pekerjaan='Cek unit', hasil='major_issue', catatan_tambahan='Kompresor mati')
        draft = BeritaAcara.objects.get(jenis='kerusakan', status='draft')
        item = draft.items.get()
        self.assertIn('Kompresor mati', item.kerusakan)

        self.client.force_login(self.ga_user)
        self.client.post(f'/berita-acara/{draft.pk}/', {
            f'kerusakan-{item.pk}': 'Kompresor mati total', f'rekomendasi-{item.pk}': 'hapus',
            'aksi': 'terbitkan', 'tanggal': date.today().isoformat()})
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'terbit')
        self.assertRegex(draft.nomor, r'^001/BIB/-HRGA/BA/[IVX]+/\d{4}$')
        self.assertEqual(len(draft.penanda_tangan), PenandaTanganBA.objects.count())   # salinan saat terbit

        # Belum ditandatangani → penghapusan ditolak; setelah ditandatangani → write off + BA Penghapusan
        self.client.post(f'/berita-acara/{draft.pk}/penghapusan/', {'item': item.pk})
        self.assertEqual(Asset.objects.get(pk=self.asset.pk).lifecycle_status, 'active')
        self.client.post(f'/berita-acara/{draft.pk}/ditandatangani/')
        self.client.post(f'/berita-acara/{draft.pk}/penghapusan/', {
            'item': item.pk, 'tanggal': date.today().isoformat(), 'alasan': 'rusak',
            'keterangan': 'Sesuai BA kerusakan', 'konfirmasi': 'on'})
        self.assertEqual(Asset.objects.get(pk=self.asset.pk).lifecycle_status, 'writeoff')
        hapus = BeritaAcara.objects.get(jenis='penghapusan')
        self.assertEqual(hapus.rujukan, draft)
        self.assertTrue(hapus.nomor.startswith('002/'))
        self.assertEqual(self.client.get(f'/berita-acara/{hapus.pk}/cetak/').status_code, 200)

    def test_form_errors_are_tagged_per_field(self):
        self.client.force_login(self.ga_user)
        r = self.client.post('/asset/create/', {'no_po': 'PO', 'nama_asset': 'X',
                                                'asset_number': 'AC-1', 'tanggal_pembelian': '2025-01-01'})
        tags = [m.extra_tags for m in r.context['messages']]
        self.assertIn('field:asset_number|', ' '.join(tags))   # ditempel di bawah isian nomor aset

    def test_holder_single_source(self):
        """Semua jalur (serah terima, movement, edit/hapus movement, kembali) menjaga assignment = movement."""
        from django.db import IntegrityError, transaction
        from .models import AssetAssignment, AssetMovement

        def holder():
            mv = AssetMovement.objects.filter(asset=self.asset, is_current=True).first()
            aktif = list(AssetAssignment.objects.filter(asset=self.asset, status='active'))
            self.assertLessEqual(len(aktif), 1)
            self.assertEqual(aktif[0].assigned_to_id if aktif else None, mv.held_by_id if mv else None)
            return mv.held_by_id if mv else None

        _, lain = make_employee('lain', UserProfile)
        self.client.force_login(self.ga_user)
        self.client.post('/ga/assignment/buat/', {'asset_id': self.asset.pk, 'assigned_to': self.usr.pk})
        self.assertEqual(holder(), self.usr.pk)

        # Pindah pemegang lewat Pelacakan → assignment lama 'transferred', yang baru aktif
        self.client.post(f'/movement/{self.asset.pk}/create/', {'held_by': lain.pk})
        self.assertEqual(holder(), lain.pk)
        self.assertTrue(AssetAssignment.objects.filter(assigned_to=self.usr, status='transferred').exists())

        # Edit movement aktif mengganti pemegang
        mv = AssetMovement.objects.get(asset=self.asset, is_current=True)
        self.client.post(f'/movement/{mv.pk}/update/', {'held_by': self.usr.pk})
        self.assertEqual(holder(), self.usr.pk)

        # Hapus movement aktif → movement sebelumnya (dan pemegangnya) aktif lagi
        self.client.post(f'/movement/{mv.pk}/delete/')
        self.assertEqual(holder(), self.usr.pk)

        # Kembalikan → tidak ada pemegang
        a = AssetAssignment.objects.get(asset=self.asset, status='active')
        self.client.post(f'/ga/assignment/{a.pk}/kembalikan/', {'return_notes': 'selesai'})
        self.assertIsNone(holder())
        a.refresh_from_db()
        self.assertEqual(a.status, 'returned')
        self.assertIn('[Dikembalikan: selesai]', a.notes)

        # Constraint DB: tidak mungkin ada 2 assignment aktif / 2 movement aktif
        AssetAssignment.objects.create(asset=self.asset, assigned_to=self.usr)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetAssignment.objects.create(asset=self.asset, assigned_to=lain)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetMovement.objects.create(asset=self.asset, is_current=True)

    def test_import_group_without_category_goes_to_lainnya(self):
        import io
        from openpyxl import Workbook
        from .models import AssetCategory
        wb = Workbook(); ws = wb.active
        ws.append(['no_po', 'asset_number', 'nama_asset', 'tanggal_pembelian', 'asset_group', 'asset_category'])
        ws.append(['PO-9', 'AC-9', 'Kipas', '2025-01-01', 'AC', ''])
        buf = io.BytesIO(); wb.save(buf); buf.seek(0); buf.name = 'aset.xlsx'
        self.client.force_login(self.ga_user)
        self.client.post('/asset/import/', {'excel_file': buf})
        asset = Asset.objects.get(asset_number='AC-9')
        self.assertEqual(asset.asset_category, AssetCategory.objects.get(group=self.cat.group, name='Lainnya'))

    def test_asset_stays_in_maintenance_while_other_reports_open(self):
        """Corrective selesai hanya mengaktifkan aset bila tidak ada laporan kerusakan lain yang berjalan."""
        def status():
            self.asset.refresh_from_db()
            return self.asset.lifecycle_status

        r1 = DamageReport.objects.create(asset=self.asset, reported_by=self.usr, title='Bocor', description='-')
        r2 = DamageReport.objects.create(asset=self.asset, reported_by=self.usr, title='Berisik', description='-')
        self.assertEqual(status(), 'maintenance')

        # Perbaikan corrective di luar alur laporan → laporan masih terbuka, aset tetap maintenance
        MaintenanceRecord.objects.create(asset=self.asset, maintenance_type='corrective',
                                         tanggal_pelaksanaan=date.today(), deskripsi_pekerjaan='Ganti seal')
        self.assertEqual(status(), 'maintenance')

        self.client.force_login(self.ga_user)
        resolve = {'tanggal_pelaksanaan': date.today().isoformat(), 'deskripsi_pekerjaan': 'fix', 'hasil': 'ok'}
        self.client.post(f'/ga/laporan-kerusakan/{r1.pk}/resolve/', resolve)
        r1.refresh_from_db()
        self.assertEqual(r1.report_status, 'resolved')
        self.assertEqual(status(), 'maintenance')          # r2 masih berjalan

        self.client.post(f'/ga/laporan-kerusakan/{r2.pk}/resolve/', resolve)
        self.assertEqual(status(), 'active')               # laporan terakhir selesai

    def test_reject_last_report_reactivates_but_writeoff_stays(self):
        r1 = DamageReport.objects.create(asset=self.asset, reported_by=self.usr, title='A', description='-')
        r2 = DamageReport.objects.create(asset=self.asset, reported_by=self.usr, title='B', description='-')
        self.client.force_login(self.ga_user)
        self.client.post(f'/ga/laporan-kerusakan/{r1.pk}/reject/')
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.lifecycle_status, 'maintenance')
        self.client.post(f'/ga/laporan-kerusakan/{r2.pk}/reject/')
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.lifecycle_status, 'active')

        # Aset write off tidak boleh "hidup lagi" karena ada catatan corrective
        Asset.objects.filter(pk=self.asset.pk).update(lifecycle_status='writeoff')
        MaintenanceRecord.objects.create(asset=self.asset, maintenance_type='corrective',
                                         tanggal_pelaksanaan=date.today(), deskripsi_pekerjaan='-')
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.lifecycle_status, 'writeoff')

    def test_all_simple_pages_render(self):
        """Semua halaman tanpa parameter URL terbuka tanpa error 500 untuk tiap peran."""
        from django.urls import get_resolver, reverse
        names = [p.name for p in get_resolver('bibasset.urls').url_patterns
                 if p.name and not p.pattern.converters and p.name not in {'logout'}]
        self.assertGreater(len(names), 20)
        for user in (self.ga_user, self.fin_user, self.usr_user):
            self.client.force_login(user)
            for name in names:
                with self.subTest(user=user.username, page=name):
                    self.assertLess(self.client.get(reverse(name)).status_code, 500)

    def test_maintenance_blocked_for_writeoff_asset(self):
        Asset.objects.filter(pk=self.asset.pk).update(lifecycle_status='writeoff')
        self.client.force_login(self.ga_user)
        r = self.client.post(f'/maintenance/asset/{self.asset.pk}/create/', {
            'maintenance_type': 'corrective', 'tanggal_pelaksanaan': '2025-06-01', 'deskripsi_pekerjaan': '-', 'hasil': 'ok'})
        self.assertRedirects(r, f'/asset/{self.asset.pk}/detail/', fetch_redirect_response=False)
        self.assertFalse(MaintenanceRecord.objects.filter(asset=self.asset).exists())

    def test_import_finansial_lists_skipped_rows(self):
        import io
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(['asset_number', 'nilai_awal_aset', 'nilai_residual', 'umur_ekonomis'])
        ws.append(['AC-1', 10_000_000, 1_000_000, 5])
        ws.append(['TIDAK-ADA', 5_000_000, 0, 4])
        buf = io.BytesIO(); wb.save(buf); buf.seek(0); buf.name = 'fin.xlsx'
        self.client.force_login(self.ga_user)
        r = self.client.post('/asset/import/finansial/', {'excel_file': buf})
        self.assertContains(r, "Aset &#x27;TIDAK-ADA&#x27; tidak ditemukan di sistem")
        self.assertTrue(AssetFinancial.objects.filter(asset=self.asset).exists())

    def test_damage_report_any_asset_without_assignment(self):
        """Karyawan boleh melapor aset yang tidak ia pegang; duplikat & aset write off ditolak."""
        self.client.force_login(self.usr_user)
        r = self.client.get('/user/laporan-kerusakan/cari-aset/?q=AC-1')
        self.assertEqual(r.json()['results'][0]['id'], self.asset.pk)
        self.assertEqual(r.json()['results'][0]['blocked'], '')
        self.assertEqual(self.client.get(f'/user/laporan-kerusakan/buat/{self.asset.pk}/').status_code, 200)
        self.client.post('/user/laporan-kerusakan/buat/', {
            'asset_id': self.asset.pk, 'title': 'Bocor', 'description': '-', 'severity': 'medium'})
        report = DamageReport.objects.get()
        self.assertEqual(report.reported_by, self.usr)
        # Laporan kedua untuk aset yang sama ditolak, dan pencarian menandainya
        self.client.post('/user/laporan-kerusakan/buat/', {
            'asset_id': self.asset.pk, 'title': 'Lagi', 'description': '-', 'severity': 'low'})
        self.assertEqual(DamageReport.objects.count(), 1)
        self.assertIn(f'#{report.pk}', self.client.get('/user/laporan-kerusakan/cari-aset/?q=AC-1').json()['results'][0]['blocked'])
        # Aset write off tidak muncul dan tidak bisa dilaporkan
        Asset.objects.filter(pk=self.asset.pk).update(lifecycle_status='writeoff')
        DamageReport.objects.update(report_status='resolved')
        self.assertEqual(self.client.get('/user/laporan-kerusakan/cari-aset/?q=AC-1').json()['results'], [])
        self.client.post('/user/laporan-kerusakan/buat/', {
            'asset_id': self.asset.pk, 'title': 'X', 'description': '-', 'severity': 'low'})
        self.assertEqual(DamageReport.objects.count(), 1)
        # Pencarian hanya untuk peran Karyawan
        self.client.force_login(self.fin_user)
        self.assertEqual(self.client.get('/user/laporan-kerusakan/cari-aset/?q=AC').status_code, 302)

    def test_employee_admin_is_ga_only(self):
        self.client.force_login(self.fin_user)
        self.assertEqual(self.client.get('/ga/karyawan/').status_code, 302)
        self.client.post('/ga/karyawan/tambah/', {'nama': 'B', 'employee_id': 'B1', 'jabatan': '-',
                                                  'username': 'b', 'access': ['user']})
        self.assertFalse(Employee.objects.filter(employee_id='B1').exists())

        self.client.force_login(self.ga_user)
        r = self.client.post('/ga/karyawan/tambah/', {'nama': 'Budi', 'employee_id': 'B1', 'jabatan': 'Staff',
                                                      'username': 'budi', 'access': ['user']})
        budi = Employee.objects.get(employee_id='B1')
        self.assertRedirects(r, f'/ga/karyawan/{budi.pk}/', fetch_redirect_response=False)
        # Password otomatis tampil sekali (tersembunyi, dengan tombol mata), lalu hilang
        page = self.client.get(f'/ga/karyawan/{budi.pk}/').content.decode()
        self.assertIn('type="password" id="credPassword"', page)
        self.assertNotIn('id="credPassword"', self.client.get(f'/ga/karyawan/{budi.pk}/').content.decode())
        self.client.post(f'/ga/karyawan/{budi.pk}/password/', {'password': 'BaruSekali9'})
        self.assertTrue(User.objects.get(username='budi').check_password('BaruSekali9'))
        # GA tidak bisa mencabut akses GA, menonaktifkan, atau menghapus akunnya sendiri
        self.client.post(f'/ga/karyawan/{self.ga.pk}/', {'nama': 'ga', 'employee_id': 'ga', 'jabatan': '-',
                                                       'username': 'ga', 'access': ['user']})
        self.assertTrue(GAProfile.objects.filter(employee=self.ga).exists())
        self.client.post(f'/ga/karyawan/{self.ga.pk}/status/')
        self.client.post(f'/ga/karyawan/{self.ga.pk}/hapus/')
        self.assertTrue(User.objects.get(pk=self.ga_user.pk).is_active)
        self.client.post(f'/ga/karyawan/{budi.pk}/hapus/')
        self.assertFalse(Employee.objects.filter(pk=budi.pk).exists())
