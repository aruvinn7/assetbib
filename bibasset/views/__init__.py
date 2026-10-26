from .auth import login_view, logout_view
from .dashboard import (dashboard, location_dashboard)  
from .assets import (
    asset_list, asset_create, asset_update, asset_detail, asset_delete,
    asset_quickview, asset_search,
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
    user_dashboard, damage_report_create,
    user_damage_report_detail, user_damage_report_list,
)
from .assignment import (assignment_list, assignment_create, assignment_return)
from .import_views import (import_asset, import_finansial, download_template_asset, download_template_finansial)

from .movement import (movement_dashboard, movement_create, movement_update, movement_delete)