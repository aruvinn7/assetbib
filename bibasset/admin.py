from django.contrib import admin

from .models import (
    Asset, AssetCategory, AssetFinancial, AssetGroup, Department, Employee,
    FinanceProfile, GAProfile, Location, UserProfile,
)

admin.site.register(Employee)
admin.site.register(Department)
admin.site.register(GAProfile)
admin.site.register(FinanceProfile)
admin.site.register(Location)
admin.site.register(AssetGroup)
admin.site.register(Asset)
admin.site.register(AssetFinancial)
admin.site.register(AssetCategory)
admin.site.register(UserProfile)

