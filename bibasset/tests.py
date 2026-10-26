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
        self.assertEqual(get_repair_threshold(1_000_000)['critical'], 60)
        self.assertEqual(get_repair_threshold(5_000_000)['critical'], 75)
        self.assertEqual(get_repair_threshold(100_000_000)['critical'], 100)

    def test_compute_health(self):
        self.assertEqual(compute_health(0, 0, None)[0], 'no_data')
        self.assertEqual(compute_health(0, 5, None)[0], 'warning')
        self.assertEqual(compute_health(700_000, 0, 1_000_000)[0], 'critical')
        self.assertEqual(compute_health(100_000, 0, 1_000_000)[0], 'normal')
        self.assertEqual(compute_health(100_000, 5, 1_000_000)[0], 'warning')

    def test_sql_critical_filter_matches_python(self):
        group = AssetGroup.objects.create(name='IT')
        for i, biaya in enumerate([700_000, 100_000]):
            a = Asset.objects.create(no_po='PO', asset_number=f'A{i}', nama_asset='x',
                                     tanggal_pembelian=date(2024, 1, 1), asset_group=group)
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
                                          asset_group=group, asset_category=self.cat)

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

    def test_form_errors_are_tagged_per_field(self):
        self.client.force_login(self.ga_user)
        r = self.client.post('/asset/create/', {'no_po': 'PO', 'nama_asset': 'X',
                                                'asset_number': 'AC-1', 'tanggal_pembelian': '2025-01-01'})
        tags = [m.extra_tags for m in r.context['messages']]
        self.assertIn('field:asset_number|', ' '.join(tags))   # ditempel di bawah isian nomor aset
