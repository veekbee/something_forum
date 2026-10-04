from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        from django.db.models.signals import post_save

        from core.models import Notification
        from core.notifications import on_created

        def created(sender, instance, created, **kwargs):
            if created:
                on_created(instance)

        post_save.connect(created, sender=Notification, dispatch_uid="core.notification_created", weak=False)
