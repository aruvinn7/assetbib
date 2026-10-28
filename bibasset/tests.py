from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .models import (
    Asset, AssetAssignment, AssetCategory, AssetFinancial, AssetGroup, AssetMovement, DamageReport,
    Department, Employee, FinanceProfile, GAProfile, Location, MaintenanceRecord, MovementReport,
    UserProfile,
    compute_health, get_repair_threshold,
)
from .views.import_views import _parse_decimal


def make_employee(username, profile=None):
    user = User.objects.create_user(username, password='x')
    emp = Employee.objects.create(user=user, employee_id=username, nama=username, jabatan='-')
    if profile:
        profile.objects.create(employee=emp)
    return user, emp


class LifeReviewTest(TestCase):
    """Umur ekonomis habis bukan alasan write off; yang diganti adalah aset tua yang juga bermasalah."""

    def review(self, years_old, life, biaya, breakdown, recent):
        from .models import health_cause, life_review
        asset = Asset(tanggal_pembelian=date.today() - timedelta(days=int(365.25 * years_old)))
        fin = AssetFinancial(asset=asset, nilai_awal_aset=Decimal(100_000_000), nilai_residual=0, umur_ekonomis=life)
        return life_review(fin, Decimal(biaya), breakdown, recent)['verdict']

    def test_verdicts(self):
        self.assertEqual(self.review(6, 4, 5_000_000, 2, 0), 'pertahankan')     # habis, sehat
        self.assertEqual(self.review(3.5, 4, 5_000_000, 1, 0), 'rencanakan')    # hampir habis, sehat
        self.assertEqual(self.review(6, 4, 40_000_000, 3, 0), 'ganti')          # habis, biaya ≥ 30%
        self.assertEqual(self.review(3.5, 4, 5_000_000, 6, 3), 'ganti')         # hampir habis, sering rusak
        self.assertEqual(self.review(1, 8, 5_000_000, 3, 2), 'periksa')         # masih baru tapi sering rusak
        self.assertEqual(self.review(1, 8, 0, 0, 0), 'normal')

    def test_warning_cause(self):
        from .models import health_cause
        self.assertEqual(health_cause(Decimal(10_000_000), 6, Decimal(100_000_000)), 'frekuensi')  # 10% < 30%, 6× rusak
        self.assertEqual(health_cause(Decimal(35_000_000), 1, Decimal(100_000_000)), 'biaya')
        self.assertIsNone(health_cause(Decimal(1_000_000), 1, Decimal(100_000_000)))


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
        self.room = Location.objects.create(name='Ruang A')

    def test_role_restrictions(self):
        self.client.force_login(self.usr_user)
        self.assertRedirects(self.client.get('/asset/'), '/user/dashboard/', fetch_redirect_response=False)
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
        dept = Department.objects.create(name='IT')
        self.client.post('/ga/assignment/buat/', {'asset_id': self.asset.pk, 'mode': 'department',
                                                  'department': dept.pk, 'location': self.room.pk})
        self.assertTrue(AssetAssignment.objects.filter(asset=self.asset, department=dept, status='active').exists())
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
        AssetCategory.objects.filter(pk=self.cat.pk).update(personal_use=True)
        self.client.force_login(self.ga_user)
        self.client.post('/ga/assignment/buat/', {'asset_id': self.asset.pk, 'mode': 'personal',
                                                  'assigned_to': self.usr.pk, 'location': self.room.pk})
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
        self.client.post(f'/ga/assignment/{a.pk}/kembalikan/', {'location': self.room.pk, 'condition': 'baik',
                                                                'notes': 'selesai'})
        self.assertIsNone(holder())
        a.refresh_from_db()
        self.assertEqual((a.status, a.return_condition), ('returned', 'baik'))
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

    def test_ga_and_finance_can_report_damage(self):
        """GA & Finance tanpa akses Karyawan tetap bisa melapor dan melihat laporannya sendiri."""
        for i, user in enumerate((self.ga_user, self.fin_user)):
            asset = Asset.objects.create(no_po='PO', asset_number=f'R-{i}', nama_asset=f'Printer {i}',
                                         tanggal_pembelian=date(2025, 1, 1), asset_category=self.cat)
            self.client.force_login(user)
            self.assertEqual(self.client.get('/user/laporan-kerusakan/cari-aset/?q=Printer').status_code, 200)
            self.client.post('/user/laporan-kerusakan/buat/', {
                'asset_id': asset.pk, 'title': 'Macet', 'description': '-', 'severity': 'low'})
            report = DamageReport.objects.get(asset=asset)
            self.assertEqual(report.reported_by.user, user)
            self.assertEqual(self.client.get(f'/user/laporan-kerusakan/{report.pk}/').status_code, 200)
            self.assertContains(self.client.get('/user/laporan-kerusakan/'), 'Macet')

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

    def test_home_per_role_and_dashboard_ga_only(self):
        """Ringkasan (/) khusus GA; login & akses ditolak mengarah ke beranda tiap peran."""
        cases = [(self.ga_user, None), (self.fin_user, '/dashboard/nilai-aset/'), (self.usr_user, '/user/dashboard/')]
        for user, home in cases:
            self.client.force_login(user)
            r = self.client.get('/')
            if home is None:
                self.assertEqual(r.status_code, 200)
            else:
                self.assertRedirects(r, home, fetch_redirect_response=False)
        # Login dengan ?next=/ (mis. lewat ngrok) tetap berakhir di beranda karyawan
        self.client.logout()
        r = self.client.post('/login/?next=/', {'username': 'usr', 'password': 'x'}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], '/user/dashboard/')
        # GA yang juga punya akses Karyawan tetap masuk ke Ringkasan
        UserProfile.objects.create(employee=self.ga)
        self.client.logout()
        self.assertRedirects(self.client.post('/login/', {'username': 'ga', 'password': 'x'}), '/',
                             fetch_redirect_response=False)
        # Akun tanpa akses apa pun tidak bisa masuk
        make_employee('kosong')
        self.client.logout()
        r = self.client.post('/login/', {'username': 'kosong', 'password': 'x'})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_master_data_and_menu_ga_only(self):
        self.client.force_login(self.fin_user)
        for url in ['/master/location/', '/master/department/', '/master/assetgroup/', '/master/assetcategory/']:
            self.assertEqual(self.client.get(url).status_code, 302, url)
        page = self.client.get('/dashboard/nilai-aset/').content.decode()
        self.assertNotIn('Master data', page)
        self.assertNotIn('>Ringkasan<', page)
        # Pelacakan/riwayat perpindahan & berita acara juga khusus GA
        for url in ['/movement/', '/berita-acara/']:
            self.assertEqual(self.client.get(url).status_code, 302, url)
        for label in ('>Pelacakan<', '>Berita acara<'):
            self.assertNotIn(label, page)
        from django.urls import reverse
        detail = self.client.get(reverse('asset_detail', args=[self.asset.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, 'Histori movement')
        self.client.force_login(self.ga_user)
        self.assertContains(self.client.get(reverse('asset_detail', args=[self.asset.pk])), 'Histori movement')
        self.client.force_login(self.fin_user)
        self.client.force_login(self.ga_user)
        self.assertEqual(self.client.get('/master/location/').status_code, 200)
        self.assertIn('Master data', self.client.get('/').content.decode())


class MovementReportTest(TestCase):
    """Karyawan melapor pindah sendiri; GA hanya menyetujui pindah departemen / pindah site."""

    def setUp(self):
        self.it, self.hse = Department.objects.create(name='IT'), Department.objects.create(name='HSE')
        self.ga_user, self.ga = make_employee('ga', GAProfile)
        self.usr_user, self.usr = make_employee('usr', UserProfile)
        self.lain_user, self.lain = make_employee('lain', UserProfile)
        self.fin_user, _ = make_employee('fin', FinanceProfile)
        Employee.objects.filter(pk=self.usr.pk).update(department=self.it)
        Employee.objects.filter(pk=self.lain.pk).update(department=self.hse)
        angsana = Location.objects.create(name='Angsana')
        self.r1 = Location.objects.create(name='Ruang 1', parent=angsana)
        self.r2 = Location.objects.create(name='Ruang 2', parent=angsana)
        self.kusan = Location.objects.create(name='Kusan')
        self.asset = Asset.objects.create(no_po='PO', asset_number='IT-1', nama_asset='Laptop',
                                          tanggal_pembelian=date(2025, 1, 1), owned_by_department=self.it,
                                          lokasi_asset=self.r1)

    def post(self, **data):
        base = {'asset_id': self.asset.pk, 'to_location': self.r1.pk, 'to_department': self.it.pk, 'to_holder': ''}
        return self.client.post('/user/lapor-pindah/buat/', {**base, **data})

    def test_room_move_recorded_immediately(self):
        self.client.force_login(self.usr_user)  # departemen pemilik, bukan pemegang
        self.post(to_location=self.r2.pk, to_holder=self.usr.pk, notes='renovasi')
        report = MovementReport.objects.get()
        self.assertEqual(report.status, 'recorded')
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.lokasi_asset, self.r2)
        mv = AssetMovement.objects.get(asset=self.asset, is_current=True)
        self.assertEqual((mv.location, mv.held_by, report.movement), (self.r2, self.usr, mv))
        self.assertEqual(AssetAssignment.objects.get(asset=self.asset, status='active').assigned_to, self.usr)

    def test_movement_history_lists_latest_move_first(self):
        tua = Asset.objects.create(no_po='PO', asset_number='IT-0', nama_asset='Aaa printer',
                                   tanggal_pembelian=date(2025, 1, 1), lokasi_asset=self.r1)
        Asset.objects.create(no_po='PO', asset_number='IT-9', nama_asset='Belum pindah',
                             tanggal_pembelian=date(2025, 1, 1))
        AssetMovement.objects.create(asset=tua, location=self.r1, moved_in=timezone.now() - timedelta(days=30))
        self.client.force_login(self.usr_user)
        self.post(to_location=self.r2.pk)  # laporan pindah terbaru
        self.client.force_login(self.ga_user)
        names = [a.nama_asset for a in self.client.get('/movement/').context['assets']]
        self.assertEqual(names, ['Laptop', 'Aaa printer', 'Belum pindah'])

    def test_department_or_site_change_waits_for_ga(self):
        self.client.force_login(self.usr_user)
        self.post(to_location=self.kusan.pk, to_department=self.hse.pk)
        report = MovementReport.objects.get()
        self.assertEqual(report.status, 'pending')
        self.assertEqual(report.reasons, 'Pindah departemen · Pindah site')
        self.asset.refresh_from_db()
        self.assertEqual((self.asset.lokasi_asset, self.asset.owned_by_department), (self.r1, self.it))
        # Laporan kedua untuk aset yang sama ditolak selama yang pertama menunggu
        self.post(to_location=self.r2.pk)
        self.assertEqual(MovementReport.objects.count(), 1)

        self.client.force_login(self.ga_user)
        self.assertContains(self.client.get('/ga/laporan-pindah/'), 'Pindah departemen')
        self.client.post(f'/ga/laporan-pindah/{report.pk}/setujui/', {'review_notes': 'ok'})
        report.refresh_from_db(); self.asset.refresh_from_db()
        self.assertEqual((report.status, report.reviewed_by), ('approved', self.ga))
        self.assertEqual((self.asset.lokasi_asset, self.asset.owned_by_department), (self.kusan, self.hse))

    def test_reject_needs_reason_and_keeps_position(self):
        self.client.force_login(self.usr_user)
        self.post(to_location=self.kusan.pk)
        report = MovementReport.objects.get()
        self.client.force_login(self.ga_user)
        self.client.post(f'/ga/laporan-pindah/{report.pk}/tolak/', {'review_notes': ''})
        report.refresh_from_db()
        self.assertEqual(report.status, 'pending')
        self.client.post(f'/ga/laporan-pindah/{report.pk}/tolak/', {'review_notes': 'Belum boleh keluar site'})
        report.refresh_from_db(); self.asset.refresh_from_db()
        self.assertEqual(report.status, 'rejected')
        self.assertEqual(self.asset.lokasi_asset, self.r1)
        self.assertFalse(AssetMovement.objects.filter(asset=self.asset).exists())

    def test_only_holder_or_owner_department_can_report(self):
        self.client.force_login(self.lain_user)  # HSE, bukan pemegang aset IT
        self.post(to_location=self.r2.pk)
        self.assertFalse(MovementReport.objects.exists())
        self.assertEqual(self.client.get('/user/lapor-pindah/cari-aset/?q=Laptop').json()['results'], [])
        # Setelah ia memegang asetnya, boleh melapor
        AssetAssignment.objects.create(asset=self.asset, assigned_to=self.lain)
        self.assertEqual(len(self.client.get('/user/lapor-pindah/cari-aset/?q=Laptop').json()['results']), 1)
        self.post(to_location=self.r2.pk, to_holder=self.lain.pk)
        self.assertEqual(MovementReport.objects.get().status, 'recorded')
        # Tanpa perubahan apa pun → ditolak
        self.post(to_location=self.r2.pk, to_holder=self.lain.pk)
        self.assertEqual(MovementReport.objects.count(), 1)
        # Finance & approve oleh non-GA tidak boleh
        self.client.force_login(self.fin_user)
        self.assertEqual(self.client.get('/user/lapor-pindah/buat/').status_code, 302)
        self.assertEqual(self.client.get('/ga/laporan-pindah/').status_code, 302)


class HandoverTest(TestCase):
    """Serah terima pribadi (kategori tertentu) atau atas nama departemen; kembali ke tempat titip GA."""

    def setUp(self):
        self.ga_user, self.ga = make_employee('ga', GAProfile)
        self.usr_user, self.usr = make_employee('usr', UserProfile)
        self.it, self.hse = Department.objects.create(name='IT'), Department.objects.create(name='HSE')
        Employee.objects.filter(pk=self.usr.pk).update(department=self.hse)
        angsana = Location.objects.create(name='Angsana')
        self.room = Location.objects.create(name='Ruang 1', parent=angsana)
        self.room2 = Location.objects.create(name='Ruang 2', parent=angsana)
        self.gudang = Location.objects.create(name='Gudang GA', parent=angsana, is_storage=True)
        Location.objects.create(name='Gudang GA', parent=Location.objects.create(name='Kusan'), is_storage=True)
        group = AssetGroup.objects.create(name='IT')
        laptop = AssetCategory.objects.create(group=group, name='Laptop', personal_use=True)
        printer = AssetCategory.objects.create(group=group, name='Printer')
        self.laptop = Asset.objects.create(no_po='PO', asset_number='LP-1', nama_asset='Laptop',
                                           tanggal_pembelian=date(2025, 1, 1), asset_category=laptop,
                                           owned_by_department=self.it, lokasi_asset=self.gudang)
        self.printer = Asset.objects.create(no_po='PO', asset_number='PR-1', nama_asset='Printer',
                                            tanggal_pembelian=date(2025, 1, 1), asset_category=printer,
                                            owned_by_department=self.it, lokasi_asset=self.room)
        self.client.force_login(self.ga_user)

    def hand_over(self, asset, **data):
        return self.client.post('/ga/assignment/buat/', {'asset_id': asset.pk, 'location': self.room.pk, **data})

    def active(self, asset):
        return AssetAssignment.objects.filter(asset=asset, status='active').first()

    def test_shared_category_cannot_go_to_a_person(self):
        self.hand_over(self.printer, mode='personal', assigned_to=self.usr.pk)
        self.assertIsNone(self.active(self.printer))
        self.hand_over(self.laptop, mode='personal', assigned_to=self.usr.pk)
        self.assertEqual(self.active(self.laptop).assigned_to, self.usr)
        self.laptop.refresh_from_db()
        self.assertEqual(self.laptop.lokasi_asset, self.room)   # keluar dari gudang

    def test_department_handover_becomes_owner_and_shows_to_staff(self):
        self.hand_over(self.printer, mode='department', department=self.hse.pk)
        a = self.active(self.printer)
        self.assertEqual((a.department, a.assigned_to), (self.hse, None))
        self.printer.refresh_from_db()
        self.assertEqual(self.printer.owned_by_department, self.hse)

        # Dipindah ruangan oleh karyawan departemennya → tetap atas nama departemen
        self.client.force_login(self.usr_user)
        self.client.post('/user/lapor-pindah/buat/', {'asset_id': self.printer.pk, 'to_location': self.room2.pk,
                                                      'to_department': self.hse.pk, 'to_holder': ''})
        self.assertEqual(self.active(self.printer).pk, a.pk)
        r = self.client.get('/user/dashboard/')
        self.assertEqual([x.asset for x in r.context['shared_assignments']], [self.printer])

        # Laporan pindah tidak boleh langsung ke gudang (itu pengembalian lewat GA)
        self.client.post('/user/lapor-pindah/buat/', {'asset_id': self.printer.pk, 'to_location': self.gudang.pk,
                                                      'to_department': self.hse.pk, 'to_holder': ''})
        self.assertEqual(MovementReport.objects.count(), 1)

    def test_return_to_storage_with_damage(self):
        self.hand_over(self.laptop, mode='personal', assigned_to=self.usr.pk)
        a = self.active(self.laptop)
        r = self.client.get(f'/ga/assignment/{a.pk}/kembalikan/')
        self.assertEqual(r.context['storage'], self.gudang)    # gudang di site yang sama, bukan Kusan

        # Rusak tanpa catatan ditolak
        self.client.post(f'/ga/assignment/{a.pk}/kembalikan/', {'location': self.gudang.pk, 'condition': 'rusak_berat'})
        self.assertIsNotNone(self.active(self.laptop))

        self.client.post(f'/ga/assignment/{a.pk}/kembalikan/', {'location': self.gudang.pk, 'condition': 'rusak_berat',
                                                                'notes': 'Layar retak'})
        a.refresh_from_db()
        self.assertEqual((a.status, a.return_condition), ('returned', 'rusak_berat'))
        self.laptop.refresh_from_db()
        self.assertEqual((self.laptop.lokasi_asset, self.laptop.lifecycle_status), (self.gudang, 'maintenance'))
        report = DamageReport.objects.get(asset=self.laptop)
        self.assertEqual((report.severity, report.description, report.report_status), ('high', 'Layar retak', 'acknowledged'))
        r = self.client.get('/ga/assignment/?tab=tersedia')
        self.assertEqual(r.context['counts']['tersedia'], 1)

    def test_storage_move_and_write_off_close_shared_handover(self):
        from .models import record_movement, write_off_asset
        self.hand_over(self.printer, mode='department', department=self.hse.pk)
        record_movement(self.printer, held_by=None, department=self.hse, location=self.gudang)
        self.assertIsNone(self.active(self.printer))

        self.hand_over(self.printer, mode='department', department=self.hse.pk)
        write_off_asset(self.printer, date.today(), 'rusak', '-', by=self.ga)
        self.assertIsNone(self.active(self.printer))
