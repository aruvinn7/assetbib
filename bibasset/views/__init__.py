from .auth import login_view, logout_view
from .dashboard import (dashboard, location_dashboard)
from .analytics import value_dashboard, performance_dashboard, life_review_dashboard  
from .assets import (
    asset_list, asset_create, asset_update, asset_detail, asset_delete,
    asset_quickview, asset_search, asset_write_off, asset_write_off_cancel,
    photo_upload, photo_set_primary, photo_delete,
)
from .master import (
    department_list, department_create, department_delete,
    location_list, location_create, location_update, location_delete, location_bulk_delete,
    assetgroup_list, assetgroup_create, assetgroup_update, assetgroup_delete,
    assetcategory_list, assetcategory_create, assetcategory_update, assetcategory_delete,
)
from .financial import financial_create, financial_update, financial_detail
from .maintenance import (
    maintenance_create, maintenance_list, maintenance_schedule_skip,
    maintenance_config_create, maintenance_config_update, maintenance_generate_schedule,
)
from .components import (
    component_list, component_create, component_update,
    component_delete, component_detail, replacement_create,
)
from .damage_reports import (
    ga_damage_report_list, ga_damage_report_detail,
    ga_damage_report_acknowledge, ga_damage_report_resolve,
    ga_damage_report_reject,
    user_dashboard, damage_report_create, damage_report_asset_search,
    user_damage_report_detail, user_damage_report_list,
)
from .assignment import (assignment_list, assignment_create, assignment_return)
from .movement_reports import (
    movement_report_create, movement_report_asset_search, movement_report_list,
    ga_movement_report_list, ga_movement_report_approve, ga_movement_report_reject,
)
from .employees import (
    employee_list, employee_create, employee_update, employee_password, employee_toggle_active, employee_delete,
)
from .import_views import (import_asset, import_finansial, download_template_asset, download_template_finansial)

from .movement import (movement_dashboard, movement_create, movement_update, movement_delete)
from .berita_acara import (
    ba_list, ba_detail, ba_item_add, ba_item_delete, ba_item_photo, ba_signed, ba_print,
    ba_penghapusan, ba_penanda_tangan,
)