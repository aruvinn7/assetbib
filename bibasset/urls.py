from django.urls import path

from . import views

urlpatterns = [

    # ===========================
    # AUTH
    # ===========================
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # ===========================
    # DASHBOARD
    # ===========================
    path('', views.dashboard, name='dashboard'),
    path('dashboard/location/', views.location_dashboard, name='location_dashboard'),
    path('dashboard/nilai-aset/', views.value_dashboard, name='value_dashboard'),
    path('dashboard/kinerja-maintenance/', views.performance_dashboard, name='performance_dashboard'),
    
    # ===========================
    # ASSET
    # ===========================
    path('asset/', views.asset_list, name='asset_list'),
    path('asset/create/', views.asset_create, name='asset_create'),
    path('asset/<int:pk>/detail/', views.asset_detail, name='asset_detail'),
    path('asset/<int:pk>/ringkas/', views.asset_quickview, name='asset_quickview'),
    path('asset/<int:pk>/write-off/', views.asset_write_off, name='asset_write_off'),
    path('asset/<int:pk>/write-off/batal/', views.asset_write_off_cancel, name='asset_write_off_cancel'),
    path('asset/cari/', views.asset_search, name='asset_search'),
    path('asset/<int:pk>/update/', views.asset_update, name='asset_update'),
    path('asset/<int:pk>/delete/', views.asset_delete, name='asset_delete'),
    
    path('asset/<int:asset_id>/photo/upload/', views.photo_upload,      name='photo_upload'),
    path('photo/<int:photo_id>/primary/',      views.photo_set_primary, name='photo_set_primary'),
    path('photo/<int:photo_id>/delete/',       views.photo_delete,      name='photo_delete'),
    

    # ===========================
    # MASTER DATA
    # ===========================

    # Department
    path('master/department/', views.department_list, name='department_list'),
    path('master/department/create/', views.department_create, name='department_create'),
    path('master/department/<int:pk>/delete/', views.department_delete, name='department_delete'),

    # Location
    path('master/location/', views.location_list, name='location_list'),
    path('master/location/create/', views.location_create, name='location_create'),
    path("master/location/<int:pk>/edit/", views.location_update, name="location_update"),
    path('master/location/<int:pk>/delete/', views.location_delete, name='location_delete'),
    path('master/location/bulk-delete/', views.location_bulk_delete, name='location_bulk_delete'),

    # Asset Group
    path('master/assetgroup/', views.assetgroup_list, name='assetgroup_list'),
    path('master/assetgroup/create/', views.assetgroup_create, name='assetgroup_create'),
    path("master/assetgroup/<int:pk>/edit/", views.assetgroup_update, name="assetgroup_update"),
    path('master/assetgroup/<int:pk>/delete/', views.assetgroup_delete, name='assetgroup_delete'),

    # Asset Category (NEW — Level 2 di bawah Asset Group)
    path('master/assetcategory/', views.assetcategory_list, name='assetcategory_list'),
    path('master/assetcategory/create/', views.assetcategory_create, name='assetcategory_create'),
    path('master/assetcategory/<int:pk>/update/', views.assetcategory_update, name='assetcategory_update'),
    path('master/assetcategory/<int:pk>/delete/', views.assetcategory_delete, name='assetcategory_delete'),

    # ===========================
    # FINANCIAL
    # ===========================
    path('financial/<int:asset_id>/create/', views.financial_create, name='financial_create'),
    path('financial/<int:asset_id>/update/', views.financial_update, name='financial_update'),
    path('financial/<int:asset_id>/detail/', views.financial_detail, name='financial_detail'),

    # ===========================
    # MAINTENANCE (GA ONLY)
    # ===========================

    # Monitoring semua jadwal (halaman utama maintenance GA)
    path('maintenance/', views.maintenance_list, name='maintenance_list'),

    # Input maintenance record untuk aset tertentu
    path('maintenance/asset/<int:asset_id>/create/', views.maintenance_create, name='maintenance_create'),

    # Skip jadwal (tandai tidak dilakukan)
    path('maintenance/schedule/<int:pk>/skip/', views.maintenance_schedule_skip, name='maintenance_schedule_skip'),

    # Override interval maintenance per-aset
    path('maintenance/config/asset/<int:asset_id>/create/', views.maintenance_config_create, name='maintenance_config_create'),
    path('maintenance/config/asset/<int:asset_id>/update/', views.maintenance_config_update, name='maintenance_config_update'),
    path('maintenance/asset/<int:asset_id>/generate/', views.maintenance_generate_schedule, name='maintenance_generate_schedule'),

    # ===========================
    # COMPONENT (GA ONLY)
    # ===========================
    path('asset/<int:asset_id>/component/', views.component_list, name='component_list'),
    path('asset/<int:asset_id>/component/create/', views.component_create, name='component_create'),
    path('component/<int:pk>/detail/', views.component_detail, name='component_detail'),
    path('component/<int:pk>/update/', views.component_update, name='component_update'),
    path('component/<int:pk>/delete/', views.component_delete, name='component_delete'),
    path('component/<int:component_id>/replacement/create/', views.replacement_create, name='replacement_create'),
    
    path('user/dashboard/',             views.user_dashboard,              name='user_dashboard'),
    path('user/laporan-kerusakan/',     views.user_damage_report_list,     name='user_damage_report_list'),
    path('user/laporan-kerusakan/buat/',         views.damage_report_create,    name='damage_report_create'),
    path('user/laporan-kerusakan/buat/<int:asset_id>/', views.damage_report_create, name='damage_report_create_for_asset'),
    path('user/laporan-kerusakan/<int:pk>/',     views.user_damage_report_detail, name='user_damage_report_detail'),

    # =============================================
    # GA — Manajemen Laporan Kerusakan
    # =============================================
    path('ga/laporan-kerusakan/',                     views.ga_damage_report_list,        name='ga_damage_report_list'),
    path('ga/laporan-kerusakan/<int:pk>/',            views.ga_damage_report_detail,      name='ga_damage_report_detail'),
    path('ga/laporan-kerusakan/<int:pk>/acknowledge/',views.ga_damage_report_acknowledge, name='ga_damage_report_acknowledge'),
    path('ga/laporan-kerusakan/<int:pk>/resolve/',    views.ga_damage_report_resolve,     name='ga_damage_report_resolve'),
    path('ga/laporan-kerusakan/<int:pk>/reject/',     views.ga_damage_report_reject,      name='ga_damage_report_reject'),

    # ===========================
    # BERITA ACARA (kerusakan & penghapusan)
    # ===========================
    path('berita-acara/',                                 views.ba_list,           name='ba_list'),
    path('berita-acara/penanda-tangan/',                  views.ba_penanda_tangan, name='ba_penanda_tangan'),
    path('berita-acara/<int:pk>/',                        views.ba_detail,         name='ba_detail'),
    path('berita-acara/<int:pk>/cetak/',                  views.ba_print,          name='ba_print'),
    path('berita-acara/<int:pk>/tambah/',                 views.ba_item_add,       name='ba_item_add'),
    path('berita-acara/<int:pk>/item/<int:item_pk>/hapus/', views.ba_item_delete,  name='ba_item_delete'),
    path('berita-acara/<int:pk>/item/<int:item_pk>/foto/',  views.ba_item_photo,   name='ba_item_photo'),
    path('berita-acara/<int:pk>/ditandatangani/',         views.ba_signed,         name='ba_signed'),
    path('berita-acara/<int:pk>/penghapusan/',            views.ba_penghapusan,    name='ba_penghapusan'),

    # =============================================
    # GA — Serah Terima Aset (Assignment)
    # =============================================
    path('ga/assignment/',                          views.assignment_list,              name='assignment_list'),
    path('ga/assignment/buat/',                     views.assignment_create,            name='assignment_create'),
    path('ga/assignment/buat/<int:asset_id>/',      views.assignment_create,            name='assignment_create_for_asset'),
    path('ga/assignment/<int:pk>/kembalikan/',      views.assignment_return,            name='assignment_return'),

    # =============================================
    # IMPORT EXCEL
    # =============================================
    path('asset/import/',                    views.import_asset,                name='import_asset'),
    path('asset/import/finansial/',          views.import_finansial,            name='import_finansial'),
    path('asset/import/template/asset/',     views.download_template_asset,     name='download_template_asset'),
    path('asset/import/template/finansial/', views.download_template_finansial, name='download_template_finansial'),

    # =============================================
    # MOVEMENT
    # =============================================
    path('movement/',                        views.movement_dashboard, name='movement_dashboard'),
    path('movement/<int:asset_id>/create/',  views.movement_create,    name='movement_create'),
    path('movement/<int:pk>/update/',        views.movement_update,    name='movement_update'),
    path('movement/<int:pk>/delete/',        views.movement_delete,    name='movement_delete'),
]