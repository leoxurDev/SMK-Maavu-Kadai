from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
import razorpay
from .models import Product, Category, PriceSlab

# Initialize Razorpay Client
razorpay_client = None
if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
    razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


# Helper to get the cart dictionary from session
def get_cart(request):
    if 'cart' not in request.session:
        request.session['cart'] = {}
    return request.session['cart']

def save_cart(request, cart):
    request.session['cart'] = cart
    request.session.modified = True

def catalog(request):
    categories = Category.objects.all().order_by('display_order', 'name_en')
    # Fetch active products with price slabs pre-fetched
    products = Product.objects.filter(is_active=True).prefetch_related('price_slabs').select_related('category')
    
    # Simple category filtering in python or django query (we will display all and scroll/filter via tabs)
    context = {
        'categories': categories,
        'products': products,
    }
    return render(request, 'shop/catalog.html', context)

@require_POST
def add_to_cart(request):
    slab_id_str = request.POST.get('slab_id')
    if not slab_id_str:
        return HttpResponse("Missing slab_id", status=400)
    
    # Verify the slab exists
    slab = get_object_or_404(PriceSlab, id=slab_id_str)
    cart = get_cart(request)
    
    # Increment quantity and apply limit check
    quantity = int(request.POST.get('quantity', 1))
    current_qty = cart.get(slab_id_str, 0)
    if current_qty + quantity > 10:
        cart[slab_id_str] = 10
        request.session['limit_exceeded'] = True
    else:
        cart[slab_id_str] = current_qty + quantity
    save_cart(request, cart)
    
    # If HTMX request, return the cart drawer snippet and trigger a badge update
    if request.htmx:
        response = HttpResponse("")
        response['HX-Trigger'] = 'cartUpdated'
        return response
        
    return redirect('catalog')

@require_POST
def update_cart_quantity(request):
    slab_id_str = request.POST.get('slab_id')
    action = request.POST.get('action') # 'increment' or 'decrement'
    
    if not slab_id_str or action not in ['increment', 'decrement']:
        return HttpResponse("Invalid request parameters", status=400)
        
    cart = get_cart(request)
    
    if slab_id_str in cart:
        if action == 'increment':
            if cart[slab_id_str] >= 10:
                request.session['limit_exceeded'] = True
            else:
                cart[slab_id_str] += 1
        elif action == 'decrement':
            cart[slab_id_str] -= 1
            if cart[slab_id_str] <= 0:
                del cart[slab_id_str]
        save_cart(request, cart)
        
    if request.htmx:
        response = HttpResponse("")
        response['HX-Trigger'] = 'cartUpdated'
        return response
        
    return redirect('catalog')

@require_POST
def remove_from_cart(request):
    slab_id_str = request.POST.get('slab_id')
    if not slab_id_str:
        return HttpResponse("Missing slab_id", status=400)
        
    cart = get_cart(request)
    if slab_id_str in cart:
        del cart[slab_id_str]
        save_cart(request, cart)
        
    if request.htmx:
        response = HttpResponse("")
        response['HX-Trigger'] = 'cartUpdated'
        return response
        
    return redirect('catalog')

def cart_badge(request):
    # Renders only the cart count badge (for header/nav)
    # The context processor takes care of cart_count
    return render(request, 'shop/partials/cart_badge.html')

def cart_drawer(request):
    # Renders the cart drawer content (usually loaded dynamically by htmx on click)
    limit_exceeded = request.session.pop('limit_exceeded', False)
    return render(request, 'shop/partials/cart_drawer_content.html', {
        'limit_exceeded': limit_exceeded
    })

from django.db import transaction
from django.utils import timezone
from .models import Customer, Address, Order, OrderItem, Payment, PriceSlab, Shop
from .context_processors import cart_processor

def cart_detail(request):
    # Fallback/standalone cart details page if needed
    return render(request, 'shop/cart_detail.html')

import math

def calculate_distance(lat1, lon1, lat2, lon2):
    try:
        lat1, lon1, lat2, lon2 = map(math.radians, [float(lat1), float(lon1), float(lat2), float(lon2)])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        r = 6371.0  # Radius of earth in kilometers
        return c * r
    except Exception:
        return 0.0

def calculate_delivery(request):
    lat = request.GET.get('latitude')
    lng = request.GET.get('longitude')
    
    # Calculate cart subtotal
    cart_data = cart_processor(request)
    cart_total = float(cart_data['cart_total'])
    
    if not lat or not lng:
        html = f"""
        <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;" id="delivery-fee-container">
            <span x-show="lang === 'en' || lang === 'both'">Delivery Fee</span>
            <span x-show="lang === 'ta' || lang === 'both'" class="tamil-text">டெலிவரி கட்டணம்</span>
            <span style="color: var(--banana-leaf); font-weight: 600;">Calculated at checkout</span>
        </div>
        <span id="grand-total-val" hx-swap-oob="true" data-cart-total="{cart_total}">₹{cart_total:.2f}</span>
        <button type="submit" id="checkout-submit-btn" hx-swap-oob="true" class="btn-primary" style="height: 48px; font-size: 1rem; background-color: var(--terracotta); box-shadow: 0 4px 6px rgba(216, 92, 56, 0.15); cursor: pointer;">
            <i class="fa-solid fa-circle-check"></i>
            <span id="submit-btn-text-en">Place Order (₹{cart_total:.2f})</span>
            <span class="tamil-text" id="submit-btn-text-ta">ஆர்டர் செய் (₹{cart_total:.2f})</span>
        </button>
        """
        return HttpResponse(html)
        
    shop = Shop.objects.first()
    if not shop or shop.latitude is None or shop.longitude is None:
        shop_lat, shop_lng = 10.987270, 76.939040
    else:
        shop_lat, shop_lng = float(shop.latitude), float(shop.longitude)
        
    distance = calculate_distance(shop_lat, shop_lng, lat, lng)
    
    if distance > 10.0:
        html = f"""
        <div style="display: flex; flex-direction: column; width: 100%; gap: 0.2rem;" id="delivery-fee-container">
            <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
                <span x-show="lang === 'en' || lang === 'both'">Delivery Fee ({distance:.2f} km)</span>
                <span x-show="lang === 'ta' || lang === 'both'" class="tamil-text">டெலிவரி கட்டணம் ({distance:.2f} கி.மீ)</span>
                <span style="font-weight: 700; color: #C0392B;">Unavailable</span>
            </div>
            <div style="color: #C0392B; font-size: 0.75rem; font-weight: 700; margin-top: 0.2rem;">
                <i class="fa-solid fa-circle-exclamation"></i>
                <span x-show="lang === 'en' || lang === 'both'">Delivery only available within 10 km from shop.</span>
                <span x-show="lang === 'ta' || lang === 'both'" class="tamil-text" style="display: block; font-size: 0.7rem; margin-top: 1px;">கடையிலிருந்து 10 கி.மீ தொலைவிற்குள் மட்டுமே டெலிவரி செய்ய முடியும்.</span>
            </div>
        </div>
        <span id="grand-total-val" hx-swap-oob="true" data-cart-total="{cart_total}">₹{cart_total:.2f}</span>
        <button type="submit" id="checkout-submit-btn" disabled hx-swap-oob="true" class="btn-primary" style="height: 48px; font-size: 1rem; background-color: var(--text-secondary); cursor: not-allowed; box-shadow: none;">
            <i class="fa-solid fa-ban"></i>
            <span x-show="lang === 'en' || lang === 'both'">Out of Delivery Range (> 10 km)</span>
            <span x-show="lang === 'ta' || lang === 'both'" class="tamil-text" style="display: block; font-size: 0.9rem;">விநியோக தூரத்தை தாண்டியது (> 10 கி.மீ)</span>
        </button>
        """
        return HttpResponse(html)
        
    delivery_fee = round(distance * 10, 2)
    final_total = cart_total + delivery_fee
    
    html = f"""
    <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;" id="delivery-fee-container">
        <span x-show="lang === 'en' || lang === 'both'">Delivery Fee ({distance:.2f} km)</span>
        <span x-show="lang === 'ta' || lang === 'both'" class="tamil-text">டெலிவரி கட்டணம் ({distance:.2f} கி.மீ)</span>
        <span style="font-weight: 700; color: var(--success-color);">₹{delivery_fee:.2f}</span>
    </div>
    <span id="grand-total-val" hx-swap-oob="true" data-cart-total="{cart_total}">₹{final_total:.2f}</span>
    <button type="submit" id="checkout-submit-btn" hx-swap-oob="true" class="btn-primary" style="height: 48px; font-size: 1rem; background-color: var(--terracotta); box-shadow: 0 4px 6px rgba(216, 92, 56, 0.15); cursor: pointer;">
        <i class="fa-solid fa-circle-check"></i>
        <span id="submit-btn-text-en">Place Order (₹{final_total:.2f})</span>
        <span class="tamil-text" id="submit-btn-text-ta">ஆர்டர் செய் (₹{final_total:.2f})</span>
    </button>
    """
    return HttpResponse(html)

def checkout(request):
    cart = get_cart(request)
    if not cart:
        return redirect('catalog')
        
    # Get cart details using context processor calculations
    cart_data = cart_processor(request)
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone_number = request.POST.get('phone_number', '').strip()
        order_type = request.POST.get('order_type', 'pickup')
        payment_method = request.POST.get('payment_method', 'cod')
        
        # Delivery fields
        address_text = request.POST.get('address_text', '').strip()
        landmark = request.POST.get('landmark', '').strip()
        latitude_str = request.POST.get('latitude', '')
        longitude_str = request.POST.get('longitude', '')
        
        # Validation
        if not name or not phone_number:
            return render(request, 'shop/checkout.html', {
                'error': 'Name and Phone number are required.',
                **cart_data
            })
            
        if order_type == 'delivery':
            if not address_text:
                return render(request, 'shop/checkout.html', {
                    'error': 'Address is required for delivery orders.',
                    **cart_data
                })
            
            # Distance range verification
            if latitude_str and longitude_str:
                try:
                    lat = float(latitude_str)
                    lng = float(longitude_str)
                    shop = Shop.objects.first()
                    if not shop or shop.latitude is None or shop.longitude is None:
                        shop_lat, shop_lng = 10.987270, 76.939040
                    else:
                        shop_lat, shop_lng = float(shop.latitude), float(shop.longitude)
                    
                    dist = calculate_distance(shop_lat, shop_lng, lat, lng)
                    if dist > 10.0:
                        return render(request, 'shop/checkout.html', {
                            'error': f'Delivery is not available beyond 10 km. Current distance: {dist:.2f} km.',
                            **cart_data
                        })
                except Exception:
                    pass
            
        try:
            with transaction.atomic():
                # 1. Get or create customer
                customer, created = Customer.objects.get_or_create(phone_number=phone_number)
                if name:
                    customer.name = name
                    customer.save()
                    
                # 2. Handle Address if delivery
                address = None
                if order_type == 'delivery':
                    lat = None
                    lng = None
                    if latitude_str:
                        lat = float(latitude_str)
                    if longitude_str:
                        lng = float(longitude_str)
                        
                    address = Address.objects.create(
                        customer=customer,
                        address_text=address_text,
                        landmark=landmark,
                        latitude=lat,
                        longitude=lng
                    )
                    
                # 3. Calculate delivery fee & distance
                delivery_fee = 0.0
                distance_km = None
                if order_type == 'delivery' and address and address.latitude is not None and address.longitude is not None:
                    shop = Shop.objects.first()
                    if shop and shop.latitude is not None and shop.longitude is not None:
                        distance_km = calculate_distance(shop.latitude, shop.longitude, address.latitude, address.longitude)
                        delivery_fee = round(distance_km * 10, 2)
                        
                order_total = float(cart_data['cart_total']) + delivery_fee

                # Create Order
                order = Order.objects.create(
                    customer=customer,
                    order_type=order_type,
                    address=address,
                    status='received',
                    total_amount=order_total,
                    delivery_fee=delivery_fee,
                    distance_km=distance_km
                )
                
                # 4. Create OrderItems
                for item in cart_data['cart_items']:
                    OrderItem.objects.create(
                        order=order,
                        product=item['product'],
                        price_slab=item['slab'],
                        quantity=item['quantity'],
                        subtotal=item['subtotal']
                    )
                    
                # 5. Create Payment record
                payment = Payment.objects.create(
                    order=order,
                    method=payment_method,
                    status='pending',
                    amount=order_total
                )
                
                # If online, initialize order in Razorpay
                if payment_method == 'online' and razorpay_client:
                    try:
                        amount_paise = int(order.total_amount * 100)
                        rzp_order_data = {
                            "amount": amount_paise,
                            "currency": "INR",
                            "receipt": f"receipt_order_{order.id}",
                            "notes": {
                                "order_id": order.id,
                                "customer_phone": customer.phone_number
                            }
                        }
                        rzp_order = razorpay_client.order.create(data=rzp_order_data)
                        payment.gateway_txn_id = rzp_order['id']
                        payment.save()
                    except Exception:
                        payment.gateway_txn_id = f"ERROR_INIT_{order.id}"
                        payment.save()
                
                # 6. Clear session cart
                request.session['cart'] = {}
                request.session.modified = True
                
                return redirect('order_tracking', order_id=order.id)
        except Exception as e:
            return render(request, 'shop/checkout.html', {
                'error': f'Something went wrong: {str(e)}',
                **cart_data
            })
            
    return render(request, 'shop/checkout.html', cart_data)

def order_tracking(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    items = order.items.select_related('product', 'price_slab').all()
    try:
        payment = order.payment
    except Payment.DoesNotExist:
        payment = None
        
    context = {
        'order': order,
        'items': items,
        'payment': payment,
        'RAZORPAY_KEY_ID': settings.RAZORPAY_KEY_ID if settings.RAZORPAY_KEY_ID else 'rzp_test_placeholder',
    }
    
    if payment and payment.status == 'pending' and payment.method == 'online':
        # Self-healing fallback if creation had failed during checkout
        if (not payment.gateway_txn_id or payment.gateway_txn_id.startswith('ERROR_INIT_')) and razorpay_client:
            try:
                amount_paise = int(order.total_amount * 100)
                rzp_order_data = {
                    "amount": amount_paise,
                    "currency": "INR",
                    "receipt": f"receipt_order_{order.id}",
                    "notes": {
                        "order_id": order.id,
                        "customer_phone": order.customer.phone_number
                    }
                }
                rzp_order = razorpay_client.order.create(data=rzp_order_data)
                payment.gateway_txn_id = rzp_order['id']
                payment.save()
            except Exception:
                pass
                
        context['total_amount_paise'] = int(order.total_amount * 100)
        
    return render(request, 'shop/order_tracking.html', context)

@require_POST
def verify_payment(request):
    payment_id = request.POST.get('razorpay_payment_id')
    rzp_order_id = request.POST.get('razorpay_order_id')
    signature = request.POST.get('razorpay_signature')
    order_id = request.POST.get('order_id')
    
    order = get_object_or_404(Order, id=order_id)
    payment = get_object_or_404(Payment, order=order)
    
    params_dict = {
        'razorpay_order_id': rzp_order_id,
        'razorpay_payment_id': payment_id,
        'razorpay_signature': signature
    }
    
    if razorpay_client and signature:
        try:
            # Verify signature
            razorpay_client.utility.verify_payment_signature(params_dict)
            
            # Payment success, update DB
            payment.status = 'completed'
            payment.paid_at = timezone.now()
            payment.save()
            
            return JsonResponse({'status': 'success', 'message': 'Payment verified successfully.'})
        except Exception as e:
            payment.status = 'failed'
            payment.save()
            return JsonResponse({'status': 'error', 'message': 'Invalid signature.'}, status=400)
    else:
        # Fallback verification for test credentials / sandbox environments
        payment.status = 'completed'
        payment.paid_at = timezone.now()
        # Mock order id for test validation
        if not payment.gateway_txn_id:
            payment.gateway_txn_id = rzp_order_id or f"MOCK_TXN_{order.id}"
        payment.save()
        return JsonResponse({'status': 'success', 'message': 'Sandbox verification success.'})

@csrf_exempt
@require_POST
def razorpay_webhook(request):
    signature = request.headers.get('X-Razorpay-Signature')
    payload = request.body
    
    import json
    try:
        event_data = json.loads(payload)
    except Exception:
        return HttpResponse("Invalid json payload", status=400)
        
    # Signature verification
    webhook_secret = getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', None)
    if webhook_secret and signature and razorpay_client:
        try:
            razorpay_client.utility.verify_webhook_signature(payload, signature, webhook_secret)
        except Exception:
            return HttpResponse("Invalid signature", status=400)
            
    # Trigger Celery background task
    from .tasks import process_payment_webhook_task
    process_payment_webhook_task.delay(event_data)
    
    return HttpResponse("OK")

def order_status_api(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    try:
        payment = order.payment
    except Payment.DoesNotExist:
        payment = None
        
    context = {
        'order': order,
        'payment': payment,
        'RAZORPAY_KEY_ID': settings.RAZORPAY_KEY_ID if settings.RAZORPAY_KEY_ID else 'rzp_test_placeholder',
    }
    if payment and payment.status == 'pending' and payment.method == 'online':
        context['total_amount_paise'] = int(order.total_amount * 100)
        
    return render(request, 'shop/partials/order_status_timeline.html', context)

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum, Count, Q

@staff_member_required
def admin_dashboard(request):
    today = timezone.localtime(timezone.now()).date()
    
    # 1. Analytics Summary Metrics
    today_sales = Payment.objects.filter(
        created_at__date=today, 
        status='completed'
    ).aggregate(total=Sum('amount'))['total'] or 0.0
    
    today_orders_count = Order.objects.filter(created_at__date=today).count()
    
    top_products = OrderItem.objects.values(
        'product__name_en', 'product__name_ta'
    ).annotate(
        total_qty=Sum('quantity')
    ).order_by('-total_qty')[:5]
    
    # 2. Live Orders List with Filters
    orders = Order.objects.select_related('customer', 'address').prefetch_related('items', 'items__product').all()
    
    # Applying filters
    status_filter = request.GET.get('status')
    if status_filter:
        orders = orders.filter(status=status_filter)
        
    type_filter = request.GET.get('order_type')
    if type_filter:
        orders = orders.filter(order_type=type_filter)
        
    search_query = request.GET.get('search')
    if search_query:
        orders = orders.filter(
            Q(id__icontains=search_query) |
            Q(customer__phone_number__icontains=search_query) |
            Q(customer__name__icontains=search_query)
        )
        
    orders = orders.order_by('-created_at')
    
    # 3. Payments for Reconciliation
    payments = Payment.objects.select_related('order', 'order__customer').all().order_by('-created_at')
    
    # 4. Active Deliveries mapping list
    active_deliveries = Order.objects.filter(
        order_type='delivery',
        status__in=['received', 'preparing', 'ready']
    ).select_related('customer', 'address').order_by('created_at')

    from django.db.models import Max
    max_order_id = Order.objects.aggregate(max_id=Max('id'))['max_id'] or 0

    context = {
        'today_sales': today_sales,
        'today_orders_count': today_orders_count,
        'top_products': top_products,
        'orders': orders,
        'payments': payments,
        'active_deliveries': active_deliveries,
        'status_choices': Order.STATUS_CHOICES,
        'max_order_id': max_order_id,
    }
    
    return render(request, 'shop/admin_dashboard.html', context)

@staff_member_required
@require_POST
def admin_update_status(request):
    order_id = request.POST.get('order_id')
    new_status = request.POST.get('status')
    order = get_object_or_404(Order, id=order_id)
    
    if new_status in dict(Order.STATUS_CHOICES):
        order.status = new_status
        order.save()
        
    if request.htmx:
        response = HttpResponse("")
        response['HX-Trigger'] = 'newOrderReceived'
        return response
        
    return redirect('admin_dashboard')

import urllib.request
import urllib.parse
import urllib.error
import json
import base64
import os
import sys

def send_sms_otp(phone_number, otp):
    sms_provider = os.getenv('SMS_PROVIDER', 'none').lower().strip()
    if sms_provider == 'none' and os.getenv('TWILIO_ACCOUNT_SID'):
        sms_provider = 'twilio'
    elif sms_provider == 'none' and os.getenv('FAST2SMS_API_KEY'):
        sms_provider = 'fast2sms'
    message = f"Your SMK Flour Shop verification code is {otp}."
    
    formatted_phone = phone_number
    if len(phone_number) == 10 and phone_number.isdigit():
        formatted_phone = "+91" + phone_number

    print(f"DEBUG SMS: Attempting to send OTP {otp} to {phone_number} using {sms_provider}", file=sys.stderr)

    if sms_provider == 'twilio':
        account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        from_phone = os.getenv('TWILIO_PHONE_NUMBER')
        
        if not account_sid or not auth_token or not from_phone:
            print("ERROR SMS: Twilio credentials missing in environment", file=sys.stderr)
            return False
            
        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        data = urllib.parse.urlencode({
            'To': formatted_phone,
            'From': from_phone,
            'Body': message
        }).encode('utf-8')
        
        req = urllib.request.Request(url, data=data, method='POST')
        auth_str = f"{account_sid}:{auth_token}"
        auth_b64 = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')
        req.add_header('Authorization', f'Basic {auth_b64}')
        
        try:
            with urllib.request.urlopen(req) as response:
                res_body = response.read().decode('utf-8')
                print("DEBUG SMS: Twilio response:", res_body, file=sys.stderr)
                return True
        except Exception as e:
            print("ERROR SMS: Failed to send via Twilio:", str(e), file=sys.stderr)
            return False

    elif sms_provider == 'fast2sms':
        api_key = os.getenv('FAST2SMS_API_KEY')
        if not api_key:
            print("ERROR SMS: Fast2SMS API key missing in environment", file=sys.stderr)
            return False
            
        raw_10_digits = phone_number[-10:]
        message = f"Your SMK Flour Shop verification code is {otp}."
        params = urllib.parse.urlencode({
            'authorization': api_key,
            'route': 'q',
            'message': message,
            'numbers': raw_10_digits,
            'language': 'english'
        })
        url = f"https://www.fast2sms.com/dev/bulkV2?{params}"
        
        try:
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req) as response:
                res_body = response.read().decode('utf-8')
                res_json = json.loads(res_body)
                print("DEBUG SMS: Fast2SMS response:", res_body, file=sys.stderr)
                return res_json.get('return', False)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8')
            print(f"ERROR SMS: Fast2SMS HTTP Error {e.code}: {e.reason} | Response Body: {err_body}", file=sys.stderr)
            return False
        except Exception as e:
            print("ERROR SMS: Failed to send via Fast2SMS:", str(e), file=sys.stderr)
            return False
            
    else:
        print("DEBUG SMS: Local sandbox fallback. OTP printed to logs.", file=sys.stderr)
        return True

import random

def customer_login(request):
    if request.method == 'POST':
        raw_phone = request.POST.get('phone_number', '').strip()
        import re
        phone_number = re.sub(r'\D', '', raw_phone)
        if len(phone_number) > 10:
            phone_number = phone_number[-10:]
            
        if not phone_number or len(phone_number) < 10:
            return render(request, 'shop/login.html', {'error': 'Please enter a valid 10-digit phone number.'})
        
        otp = str(random.randint(1000, 9999))
        request.session['login_otp'] = otp
        request.session['login_phone'] = phone_number
        
        # Send SMS OTP via Twilio / Fast2SMS
        send_sms_otp(phone_number, otp)
        
        # Security: Do not display the code in checkout/login templates if using a real SMS gateway provider!
        sms_provider = os.getenv('SMS_PROVIDER', 'none').lower().strip()
        show_preview = (sms_provider == 'none')
        
        # Print OTP to terminal to simulate SMS for console monitoring
        print("\n" + "="*50)
        print(f"[OTP SERVICE] Verification code for {phone_number} is: {otp}")
        print("="*50 + "\n")
        
        return render(request, 'shop/login.html', {
            'phone_number': phone_number,
            'otp_sent': True,
            'otp_preview': otp if show_preview else None
        })
        
    return render(request, 'shop/login.html')

def verify_otp(request):
    if request.method == 'POST':
        user_otp = request.POST.get('otp', '').strip()
        stored_otp = request.session.get('login_otp')
        phone_number = request.session.get('login_phone')
        
        if not stored_otp or not phone_number:
            return redirect('customer_login')
            
        if user_otp == stored_otp:
            customer, created = Customer.objects.get_or_create(phone_number=phone_number)
            request.session['customer_id'] = customer.id
            
            # Clean session variables
            del request.session['login_otp']
            del request.session['login_phone']
            
            return redirect('catalog')
        else:
            return render(request, 'shop/login.html', {
                'phone_number': phone_number,
                'otp_sent': True,
                'error': 'Invalid OTP code. Please check and try again.'
            })
            
    return redirect('customer_login')

def customer_logout(request):
    if 'customer_id' in request.session:
        del request.session['customer_id']
    return redirect('catalog')

def my_orders(request):
    customer_id = request.session.get('customer_id')
    if not customer_id:
        return redirect('customer_login')
        
    customer = get_object_or_404(Customer, id=customer_id)
    orders = Order.objects.filter(customer=customer).select_related('payment').prefetch_related('items', 'items__product').order_by('-created_at')
    
    return render(request, 'shop/my_orders.html', {
        'customer': customer,
        'orders': orders
    })
@require_POST
def admin_update_payment(request):
    order_id = request.POST.get('order_id')
    new_status = request.POST.get('status', 'completed')
    order = get_object_or_404(Order, id=order_id)
    
    try:
        payment = order.payment
        payment.status = new_status
        if new_status == 'completed':
            payment.paid_at = timezone.now()
        payment.save()
    except Payment.DoesNotExist:
        pass
        
    if request.htmx:
        response = HttpResponse("")
        response['HX-Trigger'] = 'newOrderReceived'
        return response
        
    return redirect('admin_dashboard')

@staff_member_required
def admin_order_notification(request):
    max_order_id = int(request.GET.get('max_order_id', 0))
    from django.db.models import Max
    current_max = Order.objects.aggregate(max_id=Max('id'))['max_id'] or 0
    
    if current_max > max_order_id:
        response = render(request, 'shop/partials/order_notification.html', {
            'new_max_id': current_max,
            'new_order': True
        })
        response['HX-Trigger'] = 'newOrderReceived'
        return response
    else:
        return render(request, 'shop/partials/order_notification.html', {
            'new_max_id': max_order_id,
            'new_order': False
        })
