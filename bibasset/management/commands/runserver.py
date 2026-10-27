from django.contrib.staticfiles.management.commands.runserver import Command as StaticfilesRunserverCommand


class Command(StaticfilesRunserverCommand):
    """`python manage.py runserver` tanpa argumen memakai port 8001, bukan bawaan Django 8000.
    Port lain tetap bisa diberikan seperti biasa, mis. `runserver 8003`."""
    default_port = '8001'
