import logging
from celery import shared_task
from django.utils import timezone
from .models import Payment

logger = logging.getLogger(__name__)

@shared_task
def process_payment_webhook_task(event_data):
    event = event_data.get('event')
    payload = event_data.get('payload', {})
    
    logger.info(f"Celery processing webhook event: {event}")
    
    if event in ['payment.captured', 'order.paid']:
        payment_entity = payload.get('payment', {}).get('entity', {})
        rzp_order_id = payment_entity.get('order_id')
        rzp_payment_id = payment_entity.get('id')
        
        if rzp_order_id:
            try:
                # Find payment by Razorpay Order ID
                payment = Payment.objects.get(gateway_txn_id=rzp_order_id)
                if payment.status != 'completed':
                    payment.status = 'completed'
                    payment.paid_at = timezone.now()
                    # Optionally store the payment transaction ID
                    payment.gateway_txn_id = rzp_order_id # Keep the order id as reference
                    payment.save()
                    logger.info(f"Payment for Razorpay Order {rzp_order_id} successfully marked as completed.")
            except Payment.DoesNotExist:
                logger.error(f"Payment record for Razorpay Order ID {rzp_order_id} not found.")
        else:
            logger.error("No order_id found in payment entity webhook payload.")

@shared_task
def send_order_status_notification_task(order_id, previous_status, current_status):
    from .models import Order
    try:
        order = Order.objects.select_related('customer').get(id=order_id)
        phone = order.customer.phone_number
        name = order.customer.name or "Customer"
        
        # English Templates
        templates_en = {
            'received': f"Hello {name}, your order #{order.id} has been received by SMK Flour Shop and is awaiting preparation.",
            'preparing': f"Hello {name}, we have started preparing your fresh items for order #{order.id}.",
            'ready': f"Hello {name}, your order #{order.id} is ready for pickup! Please collect it from Selvapuram." if order.order_type == 'pickup' else f"Hello {name}, your order #{order.id} is out for delivery!",
            'completed': f"Hello {name}, thank you for ordering from SMK Flour Shop! Your order #{order.id} is marked as completed.",
            'cancelled': f"Hello {name}, your order #{order.id} has been cancelled."
        }
        
        # Tamil Templates
        templates_ta = {
            'received': f"அன்பான {name}, உங்கள் SMK மாவு கடை ஆர்டர் #{order.id} பெறப்பட்டது. தயாரிப்பிற்காக காத்திருக்கிறது.",
            'preparing': f"அன்பான {name}, உங்கள் SMK மாவு கடை ஆர்டர் #{order.id} தயாரிப்பு தொடங்கப்பட்டுள்ளது.",
            'ready': f"அன்பான {name}, உங்கள் SMK மாவு கடை ஆர்டர் #{order.id} தயாராக உள்ளது! வந்து பெற்றுக்கொள்ளவும்." if order.order_type == 'pickup' else f"அன்பான {name}, உங்கள் SMK மாவு கடை ஆர்டர் #{order.id} டெலிவரிக்கு புறப்பட்டுவிட்டது!",
            'completed': f"அன்பான {name}, எங்களின் சேவையை பயன்படுத்தியதற்கு நன்றி! உங்கள் SMK மாவு கடை ஆர்டர் #{order.id} நிறைவடைந்தது.",
            'cancelled': f"அன்பான {name}, உங்கள் SMK மாவு கடை ஆர்டர் #{order.id} ரத்து செய்யப்பட்டுள்ளது."
        }
        
        msg_en = templates_en.get(current_status, f"Order #{order.id} status updated to {current_status}.")
        msg_ta = templates_ta.get(current_status, f"ஆர்டர் #{order.id} நிலை {current_status} ஆக மாற்றப்பட்டுள்ளது.")
        
        logger.info("-------------------- NOTIFICATION SIMULATION --------------------")
        logger.info(f"Sending SMS/WhatsApp Alert to {phone}:")
        logger.info(f"EN: {msg_en}")
        logger.info(f"TA: {msg_ta}")
        logger.info("-----------------------------------------------------------------")
        
        return True
    except Order.DoesNotExist:
        logger.error(f"Order #{order_id} not found to send status notification.")
        return False

