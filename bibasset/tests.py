from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .models import (
    Aset, SerahTerimaAset, KategoriAset, FinansialAset, GrupAset, PerpindahanAset, LaporanKerusakan,
    Departemen, Karyawan, ProfilFinance, ProfilGA, Lokasi, CatatanMaintenance, LaporanPerpindahan,
    ProfilKaryawan,
    bangun_pohon_lokasi, compute_health, get_repair_threshold,
)
from .views.import_views import _parse_decimal


def make_employee(username, profile=None):
    user = User.objects.create_user(username, password='x')
    emp = Karyawan.objects.create(user=user, nomor_karyawan=username, nama=username, jabatan='-')
    if profile:
        profile.objects.create(employee=emp)
    return user, emp


class LifeReviewTest(TestCase):
    """Umur ekonomis habis bukan alasan write off; yang diganti adalah aset tua yang juga bermasalah."""

    def review(self, years_old, life, biaya, breakdown, recent):
        from .models import health_cause, life_review
        aset = Aset(tanggal_pembelian=date.today() - timedelta(days=int(365.25 * years_old)))
        fin = FinansialAset(aset=aset, nilai_awal=Decimal(100_000_000), nilai_residual=0, umur_ekonomis=life)
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
            a = Aset.objects.create(nomor_po='PO', nomor_aset=f'A{i}', nama_aset='x',
                                     tanggal_pembelian=date(2024, 1, 1))
            FinansialAset.objects.create(aset=a, nilai_awal=1_000_000, nilai_residual=0)
            CatatanMaintenance.objects.create(aset=a, jenis_maintenance='corrective',
                                             tanggal_pelaksanaan=date(2024, 6, 1),
                                             deskripsi_pekerjaan='-', biaya=biaya)
        critical = Aset.objects.with_health_annotations().critical_health()
        self.assertEqual(list(critical.values_list('nomor_aset', flat=True)), ['A0'])


class FinancialTest(TestCase):
    def test_book_value_not_below_residual(self):
        a = Aset.objects.create(nomor_po='PO', nomor_aset='F1', nama_aset='x',
                                 tanggal_pembelian=date.today() - timedelta(days=365 * 10))
        fin = FinansialAset.objects.create(aset=a, nilai_awal=10_000_000,
                                            nilai_residual=1_000_000, umur_ekonomis=5)
        self.assertEqual(fin.book_value(), Decimal('1000000'))


class LocationPathTest(TestCase):
    def test_full_path_follows_parent_rename(self):
        root  = Lokasi.objects.create(nama='Site')
        child = Lokasi.objects.create(nama='Gedung', induk=root)
        self.assertEqual(str(child), 'Site > Gedung')
        root.nama = 'Site Baru'
        root.save()
        child.refresh_from_db()
        self.assertEqual(str(child), 'Site Baru > Gedung')

    def test_pohon_lokasi_menjumlahkan_sub_lokasi(self):
        site   = Lokasi.objects.create(nama='Site')
        gedung = Lokasi.objects.create(nama='Gedung', induk=site)
        ruang  = Lokasi.objects.create(nama='Ruang', induk=gedung)
        lain   = Lokasi.objects.create(nama='Lain')
        jumlah = {site.pk: 1, gedung.pk: 2, ruang.pk: 4, lain.pk: 0}
        locs = list(Lokasi.objects.order_by('jalur_lengkap'))
        for loc in locs:
            loc.assets = jumlah[loc.pk]

        roots, loc_map = bangun_pohon_lokasi(locs, jumlah=('assets',))

        self.assertEqual([r.nama for r in roots], ['Lain', 'Site'])
        self.assertEqual([c.nama for c in loc_map[site.pk].children_list], ['Gedung'])
        self.assertEqual(loc_map[ruang.pk].children_list, [])
        self.assertEqual(loc_map[site.pk].total_assets, 7)    # 1 + 2 + 4
        self.assertEqual(loc_map[gedung.pk].total_assets, 6)  # 2 + 4
        self.assertEqual(loc_map[ruang.pk].total_assets, 4)
        self.assertEqual(loc_map[site.pk].assets, 1)          # nilai langsung tidak berubah


class ImportParseTest(TestCase):
    def test_parse_decimal(self):
        self.assertEqual(_parse_decimal(15000000.0), Decimal('15000000.0'))
        self.assertEqual(_parse_decimal('15.000.000'), Decimal('15000000'))
        self.assertEqual(_parse_decimal('15.000.000,50'), Decimal('15000000.50'))
        self.assertEqual(_parse_decimal('Rp 2.500.000'), Decimal('2500000'))
        self.assertIsNone(_parse_decimal('abc'))


class AccessAndFlowTest(TestCase):
    def setUp(self):
        self.ga_user, self.ga = make_employee('ga', ProfilGA)
        self.fin_user, _ = make_employee('fin', ProfilFinance)
        self.usr_user, self.usr = make_employee('usr', ProfilKaryawan)
        group = GrupAset.objects.create(nama='AC')
        self.cat = KategoriAset.objects.create(grup=group, nama='Split', interval_default=3)
        self.aset = Aset.objects.create(nomor_po='PO', nomor_aset='AC-1', nama_aset='AC',
                                          tanggal_pembelian=date(2025, 1, 1),
                                          kategori_aset=self.cat)
        self.room = Lokasi.objects.create(nama='Ruang A')

    def test_role_restrictions(self):
        self.client.force_login(self.usr_user)
        self.assertRedirects(self.client.get('/asset/'), '/user/dashboard/', fetch_redirect_response=False)
        self.client.force_login(self.fin_user)
        self.client.get(f'/master/assetgroup/{self.cat.grup_id}/delete/')
        self.assertTrue(GrupAset.objects.filter(pk=self.cat.grup_id).exists())

    def test_asset_create_validation(self):
        self.client.force_login(self.ga_user)
        r = self.client.post('/asset/create/', {'nomor_po': 'PO', 'nama_aset': 'X',
                                                'nomor_aset': 'AC-1', 'tanggal_pembelian': '2025-01-01'})
        self.assertEqual(r.status_code, 200)            # re-render, bukan 500
        self.assertEqual(Aset.objects.count(), 1)

    def test_damage_report_flow(self):
        self.client.force_login(self.ga_user)
        dept = Departemen.objects.create(nama='IT')
        self.client.post('/ga/assignment/buat/', {'aset_id': self.aset.pk, 'mode': 'department',
                                                  'departemen': dept.pk, 'lokasi': self.room.pk})
        self.assertTrue(SerahTerimaAset.objects.filter(aset=self.aset, departemen=dept, status='active').exists())
        self.client.force_login(self.usr_user)
        self.client.post('/user/laporan-kerusakan/buat/', {
            'aset_id': self.aset.pk, 'judul': 'Mati', 'deskripsi': '-', 'tingkat_keparahan': 'high'})
        report = LaporanKerusakan.objects.get()
        self.aset.refresh_from_db()
        self.assertEqual(self.aset.status_siklus_hidup, 'maintenance')
        self.assertEqual(report.corrective_schedules.get().status, 'overdue')

        self.client.force_login(self.ga_user)
        self.client.post(f'/ga/laporan-kerusakan/{report.pk}/resolve/', {
            'tanggal_pelaksanaan': '2025-06-01', 'deskripsi_pekerjaan': 'fix', 'hasil': 'ok'})
        report.refresh_from_db(); self.aset.refresh_from_db()
        self.assertEqual(report.status_laporan, 'resolved')
        self.assertEqual(self.aset.status_siklus_hidup, 'active')
        self.assertEqual(report.corrective_schedules.get().status, 'done')

    def test_destructive_actions_need_post(self):
        self.client.force_login(self.ga_user)
        sch = self.aset.schedules.create(tanggal_jadwal=date.today(), status='upcoming')
        # Link GET (prefetch browser, tautan yang dibagikan) tidak boleh mengubah data
        self.assertEqual(self.client.get(f'/asset/{self.aset.pk}/delete/').status_code, 405)
        self.assertEqual(self.client.get(f'/maintenance/schedule/{sch.pk}/skip/').status_code, 405)
        self.assertTrue(Aset.objects.filter(pk=self.aset.pk).exists())
        sch.refresh_from_db()
        self.assertEqual(sch.status, 'upcoming')

        self.client.post(f'/maintenance/schedule/{sch.pk}/skip/')
        sch.refresh_from_db()
        self.assertEqual(sch.status, 'skipped')
        self.client.post(f'/asset/{self.aset.pk}/delete/')
        self.assertFalse(Aset.objects.filter(pk=self.aset.pk).exists())

    def test_berita_acara_flow(self):
        from .models import BeritaAcara, CatatanMaintenance as MR, PenandaTanganBA
        # Temuan inspeksi bermasalah → otomatis masuk draf BA Kerusakan
        MR.objects.create(aset=self.aset, jenis_maintenance='inspection', tanggal_pelaksanaan=date.today(),
                          deskripsi_pekerjaan='Cek unit', hasil='major_issue', catatan='Kompresor mati')
        draft = BeritaAcara.objects.get(jenis='kerusakan', status='draft')
        butir = draft.items.get()
        self.assertIn('Kompresor mati', butir.kerusakan)

        self.client.force_login(self.ga_user)
        self.client.post(f'/berita-acara/{draft.pk}/', {
            f'kerusakan-{butir.pk}': 'Kompresor mati total', f'rekomendasi-{butir.pk}': 'hapus',
            'aksi': 'terbitkan', 'tanggal': date.today().isoformat()})
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'terbit')
        self.assertRegex(draft.nomor, r'^001/BIB/-HRGA/BA/[IVX]+/\d{4}$')
        self.assertEqual(len(draft.penanda_tangan), PenandaTanganBA.objects.count())   # salinan saat terbit

        # Belum ditandatangani → penghapusan ditolak; setelah ditandatangani → write off + BA Penghapusan
        self.client.post(f'/berita-acara/{draft.pk}/penghapusan/', {'butir': butir.pk})
        self.assertEqual(Aset.objects.get(pk=self.aset.pk).status_siklus_hidup, 'active')
        self.client.post(f'/berita-acara/{draft.pk}/ditandatangani/')
        self.client.post(f'/berita-acara/{draft.pk}/penghapusan/', {
            'butir': butir.pk, 'tanggal': date.today().isoformat(), 'alasan': 'rusak',
            'keterangan': 'Sesuai BA kerusakan', 'konfirmasi': 'on'})
        self.assertEqual(Aset.objects.get(pk=self.aset.pk).status_siklus_hidup, 'writeoff')
        hapus = BeritaAcara.objects.get(jenis='penghapusan')
        self.assertEqual(hapus.rujukan, draft)
        self.assertTrue(hapus.nomor.startswith('002/'))
        self.assertEqual(self.client.get(f'/berita-acara/{hapus.pk}/cetak/').status_code, 200)

    def test_form_errors_are_tagged_per_field(self):
        self.client.force_login(self.ga_user)
        r = self.client.post('/asset/create/', {'nomor_po': 'PO', 'nama_aset': 'X',
                                                'nomor_aset': 'AC-1', 'tanggal_pembelian': '2025-01-01'})
        tags = [m.extra_tags for m in r.context['messages']]
        self.assertIn('field:nomor_aset|', ' '.join(tags))   # ditempel di bawah isian nomor aset

    def test_holder_single_source(self):
        """Semua jalur (serah terima, movement, edit/hapus movement, kembali) menjaga assignment = movement."""
        from django.db import IntegrityError, transaction
        from .models import SerahTerimaAset, PerpindahanAset

        def holder():
            mv = PerpindahanAset.objects.filter(aset=self.aset, posisi_saat_ini=True).first()
            aktif = list(SerahTerimaAset.objects.filter(aset=self.aset, status='active'))
            self.assertLessEqual(len(aktif), 1)
            self.assertEqual(aktif[0].diserahkan_kepada_id if aktif else None, mv.dipegang_oleh_id if mv else None)
            return mv.dipegang_oleh_id if mv else None

        _, lain = make_employee('lain', ProfilKaryawan)
        KategoriAset.objects.filter(pk=self.cat.pk).update(pemakaian_pribadi=True)
        self.client.force_login(self.ga_user)
        self.client.post('/ga/assignment/buat/', {'aset_id': self.aset.pk, 'mode': 'personal',
                                                  'diserahkan_kepada': self.usr.pk, 'lokasi': self.room.pk})
        self.assertEqual(holder(), self.usr.pk)

        # Pindah pemegang lewat Pelacakan → assignment lama 'transferred', yang baru aktif
        self.client.post(f'/movement/{self.aset.pk}/create/', {'dipegang_oleh': lain.pk})
        self.assertEqual(holder(), lain.pk)
        self.assertTrue(SerahTerimaAset.objects.filter(diserahkan_kepada=self.usr, status='transferred').exists())

        # Edit movement aktif mengganti pemegang
        mv = PerpindahanAset.objects.get(aset=self.aset, posisi_saat_ini=True)
        self.client.post(f'/movement/{mv.pk}/update/', {'dipegang_oleh': self.usr.pk})
        self.assertEqual(holder(), self.usr.pk)

        # Hapus movement aktif → movement sebelumnya (dan pemegangnya) aktif lagi
        self.client.post(f'/movement/{mv.pk}/delete/')
        self.assertEqual(holder(), self.usr.pk)

        # Kembalikan → tidak ada pemegang
        a = SerahTerimaAset.objects.get(aset=self.aset, status='active')
        self.client.post(f'/ga/assignment/{a.pk}/kembalikan/', {'lokasi': self.room.pk, 'kondisi': 'baik',
                                                                'catatan': 'selesai'})
        self.assertIsNone(holder())
        a.refresh_from_db()
        self.assertEqual((a.status, a.kondisi_kembali), ('returned', 'baik'))
        self.assertIn('[Dikembalikan: selesai]', a.catatan)

        # Constraint DB: tidak mungkin ada 2 assignment aktif / 2 movement aktif
        SerahTerimaAset.objects.create(aset=self.aset, diserahkan_kepada=self.usr)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SerahTerimaAset.objects.create(aset=self.aset, diserahkan_kepada=lain)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PerpindahanAset.objects.create(aset=self.aset, posisi_saat_ini=True)

    def test_import_group_without_category_goes_to_lainnya(self):
        import io
        from openpyxl import Workbook
        from .models import KategoriAset
        wb = Workbook(); ws = wb.active
        ws.append(['nomor_po', 'nomor_aset', 'nama_aset', 'tanggal_pembelian', 'asset_group', 'kategori_aset'])
        ws.append(['PO-9', 'AC-9', 'Kipas', '2025-01-01', 'AC', ''])
        buf = io.BytesIO(); wb.save(buf); buf.seek(0); buf.name = 'aset.xlsx'
        self.client.force_login(self.ga_user)
        self.client.post('/asset/import/', {'excel_file': buf})
        aset = Aset.objects.get(nomor_aset='AC-9')
        self.assertEqual(aset.kategori_aset, KategoriAset.objects.get(grup=self.cat.grup, nama='Lainnya'))

    def test_asset_stays_in_maintenance_while_other_reports_open(self):
        """Corrective selesai hanya mengaktifkan aset bila tidak ada laporan kerusakan lain yang berjalan."""
        def status():
            self.aset.refresh_from_db()
            return self.aset.status_siklus_hidup

        r1 = LaporanKerusakan.objects.create(aset=self.aset, dilaporkan_oleh=self.usr, judul='Bocor', deskripsi='-')
        r2 = LaporanKerusakan.objects.create(aset=self.aset, dilaporkan_oleh=self.usr, judul='Berisik', deskripsi='-')
        self.assertEqual(status(), 'maintenance')

        # Perbaikan corrective di luar alur laporan → laporan masih terbuka, aset tetap maintenance
        CatatanMaintenance.objects.create(aset=self.aset, jenis_maintenance='corrective',
                                         tanggal_pelaksanaan=date.today(), deskripsi_pekerjaan='Ganti seal')
        self.assertEqual(status(), 'maintenance')

        self.client.force_login(self.ga_user)
        resolve = {'tanggal_pelaksanaan': date.today().isoformat(), 'deskripsi_pekerjaan': 'fix', 'hasil': 'ok'}
        self.client.post(f'/ga/laporan-kerusakan/{r1.pk}/resolve/', resolve)
        r1.refresh_from_db()
        self.assertEqual(r1.status_laporan, 'resolved')
        self.assertEqual(status(), 'maintenance')          # r2 masih berjalan

        self.client.post(f'/ga/laporan-kerusakan/{r2.pk}/resolve/', resolve)
        self.assertEqual(status(), 'active')               # laporan terakhir selesai

    def test_reject_last_report_reactivates_but_writeoff_stays(self):
        r1 = LaporanKerusakan.objects.create(aset=self.aset, dilaporkan_oleh=self.usr, judul='A', deskripsi='-')
        r2 = LaporanKerusakan.objects.create(aset=self.aset, dilaporkan_oleh=self.usr, judul='B', deskripsi='-')
        self.client.force_login(self.ga_user)
        self.client.post(f'/ga/laporan-kerusakan/{r1.pk}/reject/')
        self.aset.refresh_from_db()
        self.assertEqual(self.aset.status_siklus_hidup, 'maintenance')
        self.client.post(f'/ga/laporan-kerusakan/{r2.pk}/reject/')
        self.aset.refresh_from_db()
        self.assertEqual(self.aset.status_siklus_hidup, 'active')

        # Aset write off tidak boleh "hidup lagi" karena ada catatan corrective
        Aset.objects.filter(pk=self.aset.pk).update(status_siklus_hidup='writeoff')
        CatatanMaintenance.objects.create(aset=self.aset, jenis_maintenance='corrective',
                                         tanggal_pelaksanaan=date.today(), deskripsi_pekerjaan='-')
        self.aset.refresh_from_db()
        self.assertEqual(self.aset.status_siklus_hidup, 'writeoff')

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
        Aset.objects.filter(pk=self.aset.pk).update(status_siklus_hidup='writeoff')
        self.client.force_login(self.ga_user)
        r = self.client.post(f'/maintenance/asset/{self.aset.pk}/create/', {
            'jenis_maintenance': 'corrective', 'tanggal_pelaksanaan': '2025-06-01', 'deskripsi_pekerjaan': '-', 'hasil': 'ok'})
        self.assertRedirects(r, f'/asset/{self.aset.pk}/detail/', fetch_redirect_response=False)
        self.assertFalse(CatatanMaintenance.objects.filter(aset=self.aset).exists())

    def test_import_finansial_lists_skipped_rows(self):
        import io
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(['nomor_aset', 'nilai_awal', 'nilai_residual', 'umur_ekonomis'])
        ws.append(['AC-1', 10_000_000, 1_000_000, 5])
        ws.append(['TIDAK-ADA', 5_000_000, 0, 4])
        buf = io.BytesIO(); wb.save(buf); buf.seek(0); buf.name = 'fin.xlsx'
        self.client.force_login(self.ga_user)
        r = self.client.post('/asset/import/finansial/', {'excel_file': buf})
        self.assertContains(r, "Aset &#x27;TIDAK-ADA&#x27; tidak ditemukan di sistem")
        self.assertTrue(FinansialAset.objects.filter(aset=self.aset).exists())

    def test_damage_report_any_asset_without_assignment(self):
        """Karyawan boleh melapor aset yang tidak ia pegang; duplikat & aset write off ditolak."""
        self.client.force_login(self.usr_user)
        r = self.client.get('/user/laporan-kerusakan/cari-aset/?q=AC-1')
        self.assertEqual(r.json()['results'][0]['id'], self.aset.pk)
        self.assertEqual(r.json()['results'][0]['blocked'], '')
        self.assertEqual(self.client.get(f'/user/laporan-kerusakan/buat/{self.aset.pk}/').status_code, 200)
        self.client.post('/user/laporan-kerusakan/buat/', {
            'aset_id': self.aset.pk, 'judul': 'Bocor', 'deskripsi': '-', 'tingkat_keparahan': 'medium'})
        report = LaporanKerusakan.objects.get()
        self.assertEqual(report.dilaporkan_oleh, self.usr)
        # Laporan kedua untuk aset yang sama ditolak, dan pencarian menandainya
        self.client.post('/user/laporan-kerusakan/buat/', {
            'aset_id': self.aset.pk, 'judul': 'Lagi', 'deskripsi': '-', 'tingkat_keparahan': 'low'})
        self.assertEqual(LaporanKerusakan.objects.count(), 1)
        self.assertIn(f'#{report.pk}', self.client.get('/user/laporan-kerusakan/cari-aset/?q=AC-1').json()['results'][0]['blocked'])
        # Aset write off tidak muncul dan tidak bisa dilaporkan
        Aset.objects.filter(pk=self.aset.pk).update(status_siklus_hidup='writeoff')
        LaporanKerusakan.objects.update(status_laporan='resolved')
        self.assertEqual(self.client.get('/user/laporan-kerusakan/cari-aset/?q=AC-1').json()['results'], [])
        self.client.post('/user/laporan-kerusakan/buat/', {
            'aset_id': self.aset.pk, 'judul': 'X', 'deskripsi': '-', 'tingkat_keparahan': 'low'})
        self.assertEqual(LaporanKerusakan.objects.count(), 1)

    def test_ga_and_finance_can_report_damage(self):
        """GA & Finance tanpa akses Karyawan tetap bisa melapor dan melihat laporannya sendiri."""
        for i, user in enumerate((self.ga_user, self.fin_user)):
            aset = Aset.objects.create(nomor_po='PO', nomor_aset=f'R-{i}', nama_aset=f'Printer {i}',
                                         tanggal_pembelian=date(2025, 1, 1), kategori_aset=self.cat)
            self.client.force_login(user)
            self.assertEqual(self.client.get('/user/laporan-kerusakan/cari-aset/?q=Printer').status_code, 200)
            self.client.post('/user/laporan-kerusakan/buat/', {
                'aset_id': aset.pk, 'judul': 'Macet', 'deskripsi': '-', 'tingkat_keparahan': 'low'})
            report = LaporanKerusakan.objects.get(aset=aset)
            self.assertEqual(report.dilaporkan_oleh.user, user)
            self.assertEqual(self.client.get(f'/user/laporan-kerusakan/{report.pk}/').status_code, 200)
            self.assertContains(self.client.get('/user/laporan-kerusakan/'), 'Macet')

    def test_employee_admin_is_ga_only(self):
        self.client.force_login(self.fin_user)
        self.assertEqual(self.client.get('/ga/karyawan/').status_code, 302)
        self.client.post('/ga/karyawan/tambah/', {'nama': 'B', 'nomor_karyawan': 'B1', 'jabatan': '-',
                                                  'username': 'b', 'access': ['user']})
        self.assertFalse(Karyawan.objects.filter(nomor_karyawan='B1').exists())

        self.client.force_login(self.ga_user)
        r = self.client.post('/ga/karyawan/tambah/', {'nama': 'Budi', 'nomor_karyawan': 'B1', 'jabatan': 'Staff',
                                                      'username': 'budi', 'access': ['user']})
        budi = Karyawan.objects.get(nomor_karyawan='B1')
        self.assertRedirects(r, f'/ga/karyawan/{budi.pk}/', fetch_redirect_response=False)
        # Password otomatis tampil sekali (tersembunyi, dengan tombol mata), lalu hilang
        page = self.client.get(f'/ga/karyawan/{budi.pk}/').content.decode()
        self.assertIn('type="password" id="credPassword"', page)
        self.assertNotIn('id="credPassword"', self.client.get(f'/ga/karyawan/{budi.pk}/').content.decode())
        self.client.post(f'/ga/karyawan/{budi.pk}/password/', {'password': 'BaruSekali9'})
        self.assertTrue(User.objects.get(username='budi').check_password('BaruSekali9'))
        # GA tidak bisa mencabut akses GA, menonaktifkan, atau menghapus akunnya sendiri
        self.client.post(f'/ga/karyawan/{self.ga.pk}/', {'nama': 'ga', 'nomor_karyawan': 'ga', 'jabatan': '-',
                                                       'username': 'ga', 'access': ['user']})
        self.assertTrue(ProfilGA.objects.filter(employee=self.ga).exists())
        self.client.post(f'/ga/karyawan/{self.ga.pk}/status/')
        self.client.post(f'/ga/karyawan/{self.ga.pk}/hapus/')
        self.assertTrue(User.objects.get(pk=self.ga_user.pk).is_active)
        self.client.post(f'/ga/karyawan/{budi.pk}/hapus/')
        self.assertFalse(Karyawan.objects.filter(pk=budi.pk).exists())

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
        ProfilKaryawan.objects.create(employee=self.ga)
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
        detail = self.client.get(reverse('asset_detail', args=[self.aset.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, 'Histori movement')
        self.client.force_login(self.ga_user)
        self.assertContains(self.client.get(reverse('asset_detail', args=[self.aset.pk])), 'Histori movement')
        self.client.force_login(self.fin_user)
        self.client.force_login(self.ga_user)
        self.assertEqual(self.client.get('/master/location/').status_code, 200)
        self.assertIn('Master data', self.client.get('/').content.decode())


class MovementReportTest(TestCase):
    """Karyawan melapor pindah sendiri; GA hanya menyetujui pindah departemen / pindah site."""

    def setUp(self):
        self.it, self.hse = Departemen.objects.create(nama='IT'), Departemen.objects.create(nama='HSE')
        self.ga_user, self.ga = make_employee('ga', ProfilGA)
        self.usr_user, self.usr = make_employee('usr', ProfilKaryawan)
        self.lain_user, self.lain = make_employee('lain', ProfilKaryawan)
        self.fin_user, _ = make_employee('fin', ProfilFinance)
        Karyawan.objects.filter(pk=self.usr.pk).update(departemen=self.it)
        Karyawan.objects.filter(pk=self.lain.pk).update(departemen=self.hse)
        angsana = Lokasi.objects.create(nama='Angsana')
        self.r1 = Lokasi.objects.create(nama='Ruang 1', induk=angsana)
        self.r2 = Lokasi.objects.create(nama='Ruang 2', induk=angsana)
        self.kusan = Lokasi.objects.create(nama='Kusan')
        self.aset = Aset.objects.create(nomor_po='PO', nomor_aset='IT-1', nama_aset='Laptop',
                                          tanggal_pembelian=date(2025, 1, 1), departemen_pemilik=self.it,
                                          lokasi=self.r1)

    def post(self, **data):
        base = {'aset_id': self.aset.pk, 'lokasi_tujuan': self.r1.pk, 'departemen_tujuan': self.it.pk, 'pemegang_tujuan': ''}
        return self.client.post('/user/lapor-pindah/buat/', {**base, **data})

    def test_room_move_recorded_immediately(self):
        self.client.force_login(self.usr_user)  # departemen pemilik, bukan pemegang
        self.post(lokasi_tujuan=self.r2.pk, pemegang_tujuan=self.usr.pk, catatan='renovasi')
        report = LaporanPerpindahan.objects.get()
        self.assertEqual(report.status, 'recorded')
        self.aset.refresh_from_db()
        self.assertEqual(self.aset.lokasi, self.r2)
        mv = PerpindahanAset.objects.get(aset=self.aset, posisi_saat_ini=True)
        self.assertEqual((mv.lokasi, mv.dipegang_oleh, report.perpindahan), (self.r2, self.usr, mv))
        self.assertEqual(SerahTerimaAset.objects.get(aset=self.aset, status='active').diserahkan_kepada, self.usr)

    def test_movement_history_lists_latest_move_first(self):
        tua = Aset.objects.create(nomor_po='PO', nomor_aset='IT-0', nama_aset='Aaa printer',
                                   tanggal_pembelian=date(2025, 1, 1), lokasi=self.r1)
        Aset.objects.create(nomor_po='PO', nomor_aset='IT-9', nama_aset='Belum pindah',
                             tanggal_pembelian=date(2025, 1, 1))
        PerpindahanAset.objects.create(aset=tua, lokasi=self.r1, tanggal_masuk=timezone.now() - timedelta(days=30))
        self.client.force_login(self.usr_user)
        self.post(lokasi_tujuan=self.r2.pk)  # laporan pindah terbaru
        self.client.force_login(self.ga_user)
        names = [a.nama_aset for a in self.client.get('/movement/').context['assets']]
        self.assertEqual(names, ['Laptop', 'Aaa printer', 'Belum pindah'])

    def test_department_or_site_change_waits_for_ga(self):
        self.client.force_login(self.usr_user)
        self.post(lokasi_tujuan=self.kusan.pk, departemen_tujuan=self.hse.pk)
        report = LaporanPerpindahan.objects.get()
        self.assertEqual(report.status, 'pending')
        self.assertEqual(report.alasan, 'Pindah departemen · Pindah site')
        self.aset.refresh_from_db()
        self.assertEqual((self.aset.lokasi, self.aset.departemen_pemilik), (self.r1, self.it))
        # Laporan kedua untuk aset yang sama ditolak selama yang pertama menunggu
        self.post(lokasi_tujuan=self.r2.pk)
        self.assertEqual(LaporanPerpindahan.objects.count(), 1)

        self.client.force_login(self.ga_user)
        self.assertContains(self.client.get('/ga/laporan-pindah/'), 'Pindah departemen')
        self.client.post(f'/ga/laporan-pindah/{report.pk}/setujui/', {'catatan_tinjauan': 'ok'})
        report.refresh_from_db(); self.aset.refresh_from_db()
        self.assertEqual((report.status, report.ditinjau_oleh), ('approved', self.ga))
        self.assertEqual((self.aset.lokasi, self.aset.departemen_pemilik), (self.kusan, self.hse))

    def test_reject_needs_reason_and_keeps_position(self):
        self.client.force_login(self.usr_user)
        self.post(lokasi_tujuan=self.kusan.pk)
        report = LaporanPerpindahan.objects.get()
        self.client.force_login(self.ga_user)
        self.client.post(f'/ga/laporan-pindah/{report.pk}/tolak/', {'catatan_tinjauan': ''})
        report.refresh_from_db()
        self.assertEqual(report.status, 'pending')
        self.client.post(f'/ga/laporan-pindah/{report.pk}/tolak/', {'catatan_tinjauan': 'Belum boleh keluar site'})
        report.refresh_from_db(); self.aset.refresh_from_db()
        self.assertEqual(report.status, 'rejected')
        self.assertEqual(self.aset.lokasi, self.r1)
        self.assertFalse(PerpindahanAset.objects.filter(aset=self.aset).exists())

    def test_only_holder_or_owner_department_can_report(self):
        self.client.force_login(self.lain_user)  # HSE, bukan pemegang aset IT
        self.post(lokasi_tujuan=self.r2.pk)
        self.assertFalse(LaporanPerpindahan.objects.exists())
        self.assertEqual(self.client.get('/user/lapor-pindah/cari-aset/?q=Laptop').json()['results'], [])
        # Setelah ia memegang asetnya, boleh melapor
        SerahTerimaAset.objects.create(aset=self.aset, diserahkan_kepada=self.lain)
        self.assertEqual(len(self.client.get('/user/lapor-pindah/cari-aset/?q=Laptop').json()['results']), 1)
        self.post(lokasi_tujuan=self.r2.pk, pemegang_tujuan=self.lain.pk)
        self.assertEqual(LaporanPerpindahan.objects.get().status, 'recorded')
        # Tanpa perubahan apa pun → ditolak
        self.post(lokasi_tujuan=self.r2.pk, pemegang_tujuan=self.lain.pk)
        self.assertEqual(LaporanPerpindahan.objects.count(), 1)
        # Finance & approve oleh non-GA tidak boleh
        self.client.force_login(self.fin_user)
        self.assertEqual(self.client.get('/user/lapor-pindah/buat/').status_code, 302)
        self.assertEqual(self.client.get('/ga/laporan-pindah/').status_code, 302)


class HandoverTest(TestCase):
    """Serah terima pribadi (kategori tertentu) atau atas nama departemen; kembali ke tempat titip GA."""

    def setUp(self):
        self.ga_user, self.ga = make_employee('ga', ProfilGA)
        self.usr_user, self.usr = make_employee('usr', ProfilKaryawan)
        self.it, self.hse = Departemen.objects.create(nama='IT'), Departemen.objects.create(nama='HSE')
        Karyawan.objects.filter(pk=self.usr.pk).update(departemen=self.hse)
        angsana = Lokasi.objects.create(nama='Angsana')
        self.room = Lokasi.objects.create(nama='Ruang 1', induk=angsana)
        self.room2 = Lokasi.objects.create(nama='Ruang 2', induk=angsana)
        self.gudang = Lokasi.objects.create(nama='Gudang GA', induk=angsana, tempat_penyimpanan=True)
        Lokasi.objects.create(nama='Gudang GA', induk=Lokasi.objects.create(nama='Kusan'), tempat_penyimpanan=True)
        group = GrupAset.objects.create(nama='IT')
        laptop = KategoriAset.objects.create(grup=group, nama='Laptop', pemakaian_pribadi=True)
        printer = KategoriAset.objects.create(grup=group, nama='Printer')
        self.laptop = Aset.objects.create(nomor_po='PO', nomor_aset='LP-1', nama_aset='Laptop',
                                           tanggal_pembelian=date(2025, 1, 1), kategori_aset=laptop,
                                           departemen_pemilik=self.it, lokasi=self.gudang)
        self.printer = Aset.objects.create(nomor_po='PO', nomor_aset='PR-1', nama_aset='Printer',
                                            tanggal_pembelian=date(2025, 1, 1), kategori_aset=printer,
                                            departemen_pemilik=self.it, lokasi=self.room)
        self.client.force_login(self.ga_user)

    def hand_over(self, asset, **data):
        return self.client.post('/ga/assignment/buat/', {'aset_id': asset.pk, 'lokasi': self.room.pk, **data})

    def active(self, asset):
        return SerahTerimaAset.objects.filter(aset=asset, status='active').first()

    def test_shared_category_cannot_go_to_a_person(self):
        self.hand_over(self.printer, mode='personal', diserahkan_kepada=self.usr.pk)
        self.assertIsNone(self.active(self.printer))
        self.hand_over(self.laptop, mode='personal', diserahkan_kepada=self.usr.pk)
        self.assertEqual(self.active(self.laptop).diserahkan_kepada, self.usr)
        self.laptop.refresh_from_db()
        self.assertEqual(self.laptop.lokasi, self.room)   # keluar dari gudang

    def test_department_handover_becomes_owner_and_shows_to_staff(self):
        self.hand_over(self.printer, mode='department', departemen=self.hse.pk)
        a = self.active(self.printer)
        self.assertEqual((a.departemen, a.diserahkan_kepada), (self.hse, None))
        self.printer.refresh_from_db()
        self.assertEqual(self.printer.departemen_pemilik, self.hse)

        # Dipindah ruangan oleh karyawan departemennya → tetap atas nama departemen
        self.client.force_login(self.usr_user)
        self.client.post('/user/lapor-pindah/buat/', {'aset_id': self.printer.pk, 'lokasi_tujuan': self.room2.pk,
                                                      'departemen_tujuan': self.hse.pk, 'pemegang_tujuan': ''})
        self.assertEqual(self.active(self.printer).pk, a.pk)
        r = self.client.get('/user/dashboard/')
        self.assertEqual([x.aset for x in r.context['shared_assignments']], [self.printer])

        # Laporan pindah tidak boleh langsung ke gudang (itu pengembalian lewat GA)
        self.client.post('/user/lapor-pindah/buat/', {'aset_id': self.printer.pk, 'lokasi_tujuan': self.gudang.pk,
                                                      'departemen_tujuan': self.hse.pk, 'pemegang_tujuan': ''})
        self.assertEqual(LaporanPerpindahan.objects.count(), 1)

    def test_return_to_storage_with_damage(self):
        self.hand_over(self.laptop, mode='personal', diserahkan_kepada=self.usr.pk)
        a = self.active(self.laptop)
        r = self.client.get(f'/ga/assignment/{a.pk}/kembalikan/')
        self.assertEqual(r.context['storage'], self.gudang)    # gudang di site yang sama, bukan Kusan

        # Rusak tanpa catatan ditolak
        self.client.post(f'/ga/assignment/{a.pk}/kembalikan/', {'lokasi': self.gudang.pk, 'kondisi': 'rusak_berat'})
        self.assertIsNotNone(self.active(self.laptop))

        self.client.post(f'/ga/assignment/{a.pk}/kembalikan/', {'lokasi': self.gudang.pk, 'kondisi': 'rusak_berat',
                                                                'catatan': 'Layar retak'})
        a.refresh_from_db()
        self.assertEqual((a.status, a.kondisi_kembali), ('returned', 'rusak_berat'))
        self.laptop.refresh_from_db()
        self.assertEqual((self.laptop.lokasi, self.laptop.status_siklus_hidup), (self.gudang, 'maintenance'))
        report = LaporanKerusakan.objects.get(aset=self.laptop)
        self.assertEqual((report.tingkat_keparahan, report.deskripsi, report.status_laporan), ('high', 'Layar retak', 'acknowledged'))
        r = self.client.get('/ga/assignment/?tab=tersedia')
        self.assertEqual(r.context['counts']['tersedia'], 1)

    def test_storage_move_and_write_off_close_shared_handover(self):
        from .models import record_movement, write_off_asset
        self.hand_over(self.printer, mode='department', departemen=self.hse.pk)
        record_movement(self.printer, dipegang_oleh=None, departemen=self.hse, lokasi=self.gudang)
        self.assertIsNone(self.active(self.printer))

        self.hand_over(self.printer, mode='department', departemen=self.hse.pk)
        write_off_asset(self.printer, date.today(), 'rusak', '-', by=self.ga)
        self.assertIsNone(self.active(self.printer))


DB_STORAGE = {
    'default': {'BACKEND': 'bibasset.storage.DatabaseStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}


def png_bytes():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), (70, 95, 255)).save(buf, 'PNG')
    return buf.getvalue()


class DatabaseMediaTest(TestCase):
    """Server tanpa layanan penyimpanan terpisah: file unggahan disimpan & disajikan dari database."""

    def setUp(self):
        self.ga_user, self.ga = make_employee('ga', ProfilGA)
        self.usr_user, _ = make_employee('usr', ProfilKaryawan)
        self.aset = Aset.objects.create(nomor_po='PO', nomor_aset='AC-1', nama_aset='AC',
                                         tanggal_pembelian=date(2025, 1, 1))

    def test_storage_roundtrip(self):
        from django.core.files.base import ContentFile
        from .models import BerkasMedia
        from .storage import DatabaseStorage
        st = DatabaseStorage()
        name = st.save('asset_photos/a.png', ContentFile(png_bytes()))
        self.assertEqual(name, 'asset_photos/a.png')
        again = st.save('asset_photos/a.png', ContentFile(b'lain'))
        self.assertNotEqual(again, name)                       # tidak menimpa, diberi akhiran
        self.assertTrue(st.exists(name))
        self.assertEqual(st.size(name), len(png_bytes()))
        self.assertEqual(st.open(name).read(), png_bytes())
        self.assertEqual(st.url(name), '/berkas/asset_photos/a.png')
        self.assertEqual(st.listdir('asset_photos'), ([], sorted(['a.png', again.split('/')[-1]])))
        st.delete(name)
        self.assertFalse(st.exists(name))
        self.assertEqual(BerkasMedia.objects.count(), 1)

    def test_serve_requires_login_and_blocks_html(self):
        from django.core.files.base import ContentFile
        from .storage import DatabaseStorage
        st = DatabaseStorage()
        st.save('x/foto.png', ContentFile(png_bytes()))
        st.save('x/jahat.html', ContentFile(b'<script>alert(1)</script>'))
        self.assertEqual(self.client.get('/berkas/x/foto.png').status_code, 302)   # belum login
        self.client.force_login(self.usr_user)
        r = self.client.get('/berkas/x/foto.png')
        self.assertEqual((r.status_code, r['Content-Type']), (200, 'image/png'))
        self.assertTrue(r['Content-Disposition'].startswith('inline'))
        self.assertEqual(self.client.get('/berkas/x/foto.png', HTTP_IF_NONE_MATCH=r['ETag']).status_code, 304)
        r = self.client.get('/berkas/x/jahat.html')
        self.assertEqual(r['Content-Type'], 'application/octet-stream')
        self.assertTrue(r['Content-Disposition'].startswith('attachment'))
        self.assertEqual(self.client.get('/berkas/x/tidak-ada.png').status_code, 404)

    def test_photo_upload_primary_and_delete(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from .models import FotoAset, BerkasMedia
        self.client.force_login(self.ga_user)
        with override_settings(STORAGES=DB_STORAGE):
            for i in range(2):
                f = SimpleUploadedFile(f'p{i}.png', png_bytes(), content_type='image/png')
                self.client.post(f'/asset/{self.aset.pk}/photo/upload/',
                                 {'foto': f, 'jenis_foto': 'lokasi', 'keterangan': f'k{i}', 'foto_utama': 'on'})
            photos = FotoAset.objects.filter(aset=self.aset)
            self.assertEqual(photos.count(), 2)
            self.assertEqual(photos.filter(foto_utama=True).count(), 1)     # hanya satu foto utama
            latest = photos.get(keterangan='k1')
            self.assertTrue(latest.foto_utama)
            self.assertEqual(latest.jenis_foto, 'lokasi')
            self.assertEqual(BerkasMedia.objects.count(), 2)
            # Daftar aset memakai URL dari storage aktif, bukan MEDIA_URL
            r = self.client.get('/asset/')
            self.assertContains(r, f'src="/berkas/{latest.foto.name}"')
            self.client.post(f'/photo/{latest.pk}/delete/')
            self.assertEqual(BerkasMedia.objects.count(), 1)               # isi file ikut terhapus
            self.assertTrue(FotoAset.objects.get(aset=self.aset).foto_utama)


class SessionTimeoutTest(TestCase):
    """Sesi login berakhir setelah lama tidak aktif; sesi lama tanpa penanda ikut diakhiri."""

    def setUp(self):
        self.user, _ = make_employee('usr', ProfilKaryawan)

    def set_last_active(self, seconds_ago):
        import time
        from .session_timeout import KUNCI
        s = self.client.session
        s[KUNCI] = int(time.time()) - seconds_ago
        s.save()

    def test_idle_timeout(self):
        from django.conf import settings
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/user/dashboard/').status_code, 200)   # baru login → aktif
        self.set_last_active(settings.SESSION_IDLE_TIMEOUT - 60)
        self.assertEqual(self.client.get('/user/dashboard/').status_code, 200)   # masih dalam batas
        self.set_last_active(settings.SESSION_IDLE_TIMEOUT + 60)
        r = self.client.get('/user/dashboard/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/login/', r['Location'])
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_old_session_without_marker_is_ended(self):
        from .session_timeout import KUNCI
        self.client.force_login(self.user)
        s = self.client.session
        del s[KUNCI]
        s.save()
        self.assertEqual(self.client.get('/user/dashboard/').status_code, 302)

    def test_activity_refreshes(self):
        from .session_timeout import KUNCI, SEGARKAN
        self.client.force_login(self.user)
        self.set_last_active(SEGARKAN + 10)
        before = self.client.session[KUNCI]
        self.client.get('/user/dashboard/')
        self.assertGreater(self.client.session[KUNCI], before)
