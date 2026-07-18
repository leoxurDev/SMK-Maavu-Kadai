from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from .models import Order
from .tasks import send_order_status_notification_task

@receiver(pre_save, sender=Order)
def track_order_status_before_save(sender, instance, **kwargs):
    if instance.id:
        try:
            original = Order.objects.get(id=instance.id)
            instance._original_status = original.status
        except Order.DoesNotExist:
            instance._original_status = None
    else:
        instance._original_status = None

@receiver(post_save, sender=Order)
def trigger_status_change_notification(sender, instance, created, **kwargs):
    if created:
        # Initial status notification
        send_order_status_notification_task.delay(instance.id, None, instance.status)
    else:
        original_status = getattr(instance, '_original_status', None)
        if original_status and original_status != instance.status:
            send_order_status_notification_task.delay(instance.id, original_status, instance.status)
