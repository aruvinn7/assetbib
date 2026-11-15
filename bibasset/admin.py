from django.contrib import admin

from .models import (
    Aset, KategoriAset, FinansialAset, GrupAset, Departemen, Karyawan,
    ProfilFinance, ProfilGA, Lokasi, ProfilKaryawan,
)

admin.site.register(Karyawan)
admin.site.register(Departemen)
admin.site.register(ProfilGA)
admin.site.register(ProfilFinance)
admin.site.register(Lokasi)
admin.site.register(GrupAset)
admin.site.register(Aset)
admin.site.register(FinansialAset)
admin.site.register(KategoriAset)
admin.site.register(ProfilKaryawan)

