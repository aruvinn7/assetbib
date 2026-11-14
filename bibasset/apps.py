from django.apps import AppConfig


class BibassetConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bibasset'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in
        from .session_timeout import tandai_aktif
        user_logged_in.connect(tandai_aktif, dispatch_uid='bibasset_tandai_aktif')
