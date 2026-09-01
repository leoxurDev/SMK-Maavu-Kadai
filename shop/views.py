from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
try:
    import razorpay
except ImportError:
    razorpay = None

from .models import Product, Category, PriceSlab, Order, OrderItem, Payment, Customer, Address, DeliverySettings, CustomerOtpLog

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
    # Fetch active products with price slabs pre-fetched
    all_products = list(Product.objects.filter(is_active=True).prefetch_related('price_slabs').select_related('category'))
    
    # Filter in-stock and out-of-stock products
    in_stock_products = [p for p in all_products if p.is_in_stock]
    out_of_stock_products = [p for p in all_products if not p.is_in_stock]
    
    # Fetch all categories ordered by display_order
    categories = Category.objects.all().order_by('display_order', 'name_en')
    
    # Filter categories that contain at least one in-stock product
    active_categories = []
    for cat in categories:
        has_items = any(p.category_id == cat.id for p in in_stock_products)
        if has_items:
            active_categories.append(cat)
            
    context = {
        'categories': active_categories,
        'products': in_stock_products,
        'out_of_stock_products': out_of_stock_products,
    }
    return render(request, 'shop/catalog.html', context)



def get_available_product_stock_for_cart(product, cart, exclude_slab_id=None):
    from shop.models import normalize_to_base
    if product.inventory_type == 'bulk':
        total_base_stock = normalize_to_base(product.bulk_stock, product.bulk_unit)
        base_in_cart = 0
        for s_id, qty in cart.items():
            if s_id == exclude_slab_id:
                continue
            try:
                s = PriceSlab.objects.get(id=s_id)
                if s.product == product:
                    base_in_cart += normalize_to_base(s.quantity_value, s.quantity_unit) * qty
            except PriceSlab.DoesNotExist:
                pass
        return max(0, total_base_stock - base_in_cart)
    return None

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
    
    if slab.product.inventory_type == 'bulk':
        from shop.models import normalize_to_base
        available_base = get_available_product_stock_for_cart(slab.product, cart, exclude_slab_id=slab_id_str)
        slab_base = normalize_to_base(slab.quantity_value, slab.quantity_unit)
        
        if available_base <= 0:
            cart[slab_id_str] = 0
            request.session['stock_exceeded'] = True
        else:
            needed_base = slab_base * (current_qty + quantity)
            if needed_base > available_base:
                import math
                max_allowed = int(math.floor(available_base / slab_base))
                cart[slab_id_str] = max_allowed
                request.session['stock_exceeded'] = True
            elif current_qty + quantity > 10:
                cart[slab_id_str] = 10
                request.session['limit_exceeded'] = True
            else:
                cart[slab_id_str] = current_qty + quantity
    else:
        max_qty = slab.get_max_available_quantity()
        if current_qty + quantity > max_qty:
            cart[slab_id_str] = max_qty
            request.session['stock_exceeded'] = True
        elif current_qty + quantity > 10:
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
    slab = get_object_or_404(PriceSlab, id=slab_id_str)
    
    if slab_id_str in cart:
        if action == 'increment':
            if slab.product.inventory_type == 'bulk':
                from shop.models import normalize_to_base
                available_base = get_available_product_stock_for_cart(slab.product, cart, exclude_slab_id=slab_id_str)
                slab_base = normalize_to_base(slab.quantity_value, slab.quantity_unit)
                needed_base = slab_base * (cart[slab_id_str] + 1)
                
                if needed_base > available_base:
                    request.session['stock_exceeded'] = True
                elif cart[slab_id_str] >= 10:
                    request.session['limit_exceeded'] = True
                else:
                    cart[slab_id_str] += 1
            else:
                max_qty = slab.get_max_available_quantity()
                if cart[slab_id_str] >= max_qty:
                    request.session['stock_exceeded'] = True
                elif cart[slab_id_str] >= 10:
                    request.session['limit_exceeded'] = True
                else:
                    cart[slab_id_str] += 1
        elif action == 'decrement':
            if cart[slab_id_str] <= 1:
                cart.pop(slab_id_str)
            else:
                cart[slab_id_str] -= 1
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
    stock_exceeded = request.session.pop('stock_exceeded', False)
    return render(request, 'shop/partials/cart_drawer_content.html', {
        'limit_exceeded': limit_exceeded,
        'stock_exceeded': stock_exceeded
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
        
    delivery_settings, _ = DeliverySettings.objects.get_or_create(id=1)
    delivery_fee = round(float(delivery_settings.base_delivery_fee) + (distance * float(delivery_settings.cost_per_km)), 2)
    final_total = cart_total + delivery_fee
    
    html = f"""
    <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;" id="delivery-fee-container">
        <span x-show="lang === 'en' || lang === 'both'">Delivery Fee ({distance:.2f} km @ ₹{delivery_settings.cost_per_km}/km)</span>
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
    delivery_settings, _ = DeliverySettings.objects.get_or_create(id=1)
    cart_data['enable_online_payment'] = delivery_settings.enable_online_payment
    cart_data['delivery_settings'] = delivery_settings
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone_number = request.POST.get('phone_number', '').strip()
        order_type = request.POST.get('order_type', 'pickup')
        payment_method = request.POST.get('payment_method', 'cod')
        
        if payment_method == 'online' and not delivery_settings.enable_online_payment:
            payment_method = 'cod'

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
            
        # Verify stock availability for all items before placing the order
        # For bulk products, we must aggregate the total base weight across all slabs of the product in the cart
        product_base_totals = {}
        for item in cart_data['cart_items']:
            prod = item['product']
            slab = item['slab']
            qty = item['quantity']
            
            if prod.inventory_type == 'bulk':
                from shop.models import normalize_to_base
                base_qty = normalize_to_base(slab.quantity_value, slab.quantity_unit) * qty
                product_base_totals[prod.id] = product_base_totals.get(prod.id, 0) + base_qty
            else:
                if not slab.is_in_stock(qty):
                    return render(request, 'shop/checkout.html', {
                        'error': f"Sorry, {prod.name_en} ({slab.quantity_value} {slab.get_quantity_unit_display()}) does not have enough inventory. Only {slab.stock} units left.",
                        **cart_data
                    })
                    
        for prod_id, total_base_needed in product_base_totals.items():
            prod = Product.objects.get(id=prod_id)
            from shop.models import normalize_to_base
            total_base_stock = normalize_to_base(prod.bulk_stock, prod.bulk_unit)
            if total_base_needed > total_base_stock:
                return render(request, 'shop/checkout.html', {
                    'error': f"Sorry, {prod.name_en} does not have enough inventory for all items in your cart. Available stock: {prod.bulk_stock} {prod.bulk_unit}.",
                    **cart_data
                })

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
                    
                # 3. Calculate delivery fee & distance using DeliverySettings
                delivery_fee = 0.0
                distance_km = None
                if order_type == 'delivery' and address and address.latitude is not None and address.longitude is not None:
                    shop = Shop.objects.first()
                    if shop and shop.latitude is not None and shop.longitude is not None:
                        distance_km = calculate_distance(shop.latitude, shop.longitude, address.latitude, address.longitude)
                        delivery_fee = round(float(delivery_settings.base_delivery_fee) + (distance_km * float(delivery_settings.cost_per_km)), 2)
                        
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
                
                # 4. Create OrderItems and deduct stock
                for item in cart_data['cart_items']:
                    OrderItem.objects.create(
                        order=order,
                        product=item['product'],
                        price_slab=item['slab'],
                        quantity=item['quantity'],
                        subtotal=item['subtotal']
                    )
                    # Deduct inventory stock
                    item['slab'].deduct_stock(item['quantity'])
                    
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

    import json
    from django.db.models.functions import TruncDate, ExtractHour
    from django.db.models import Count
    
    # 7-day Daily Sales Trend
    seven_days_ago = today - timedelta(days=6)
    sales_trend_qs = Payment.objects.filter(
        created_at__date__gte=seven_days_ago,
        status='completed'
    ).annotate(date=TruncDate('created_at')).values('date').annotate(
        total=Sum('amount')
    ).order_by('date')
    
    # Pre-fill all 7 days with 0.0 to ensure continuous line chart
    sales_trend_dict = { (today - timedelta(days=i)): 0.0 for i in range(7) }
    for item in sales_trend_qs:
        if item['date'] in sales_trend_dict:
            sales_trend_dict[item['date']] = float(item['total'])
    
    sorted_trend_dates = sorted(sales_trend_dict.keys())
    sales_trend_labels = [d.strftime('%b %d') for d in sorted_trend_dates]
    sales_trend_values = [sales_trend_dict[d] for d in sorted_trend_dates]
    
    # Hourly Velocity (Today)
    hourly_qs = Order.objects.filter(
        created_at__date=today
    ).annotate(hour=ExtractHour('created_at')).values('hour').annotate(
        count=Count('id')
    ).order_by('hour')
    
    hourly_dict = { h: 0 for h in range(24) }
    for item in hourly_qs:
        hourly_dict[item['hour']] = item['count']
        
    hourly_labels = [f"{h:02d}:00" for h in range(24)]
    hourly_values = [hourly_dict[h] for h in range(24)]
    
    # Top Products
    top_products_labels = [item['product__name_en'] for item in top_products]
    top_products_values = [int(item['total_qty']) for item in top_products]
    
    sales_trend_labels_json = json.dumps(sales_trend_labels)
    sales_trend_values_json = json.dumps(sales_trend_values)
    hourly_labels_json = json.dumps(hourly_labels)
    hourly_values_json = json.dumps(hourly_values)
    top_products_labels_json = json.dumps(top_products_labels)
    top_products_values_json = json.dumps(top_products_values)
    
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

    from django.contrib.auth.models import User
    delivery_staff = User.objects.filter(
        Q(groups__name='Delivery Staff') | Q(is_staff=True)
    ).distinct().order_by('username')

    from django.db.models import Max
    max_order_id = Order.objects.aggregate(max_id=Max('id'))['max_id'] or 0

    all_products = Product.objects.all().prefetch_related('price_slabs').order_by('category__display_order', 'name_en')
    categories = Category.objects.all().order_by('display_order', 'name_en')

    from shop.models import SmtpConfig, CustomerOtpLog, DeliverySettings, WhatsAppConfig
    try:
        smtp_config = SmtpConfig.objects.first()
    except Exception:
        smtp_config = None
        
    try:
        otp_logs = CustomerOtpLog.objects.all().order_by('-created_at')[:30]
    except Exception:
        otp_logs = []
        
    delivery_settings = DeliverySettings.get_settings()
    whatsapp_config = WhatsAppConfig.get_config()

    context = {
        'today_sales': today_sales,
        'today_orders_count': today_orders_count,
        'top_products': top_products,
        'orders': orders,
        'payments': payments,
        'active_deliveries': active_deliveries,
        'status_choices': Order.STATUS_CHOICES,
        'max_order_id': max_order_id,
        'sales_trend_labels_json': sales_trend_labels_json,
        'sales_trend_values_json': sales_trend_values_json,
        'hourly_labels_json': hourly_labels_json,
        'hourly_values_json': hourly_values_json,
        'top_products_labels_json': top_products_labels_json,
        'top_products_values_json': top_products_values_json,
        'delivery_staff': delivery_staff,
        'all_products': all_products,
        'categories': categories,
        'smtp_config': smtp_config,
        'otp_logs': otp_logs,
        'delivery_settings': delivery_settings,
        'whatsapp_config': whatsapp_config,
    }
    
    return render(request, 'shop/admin_dashboard.html', context)

@staff_member_required
@require_POST
def admin_update_status(request):
    order_id = request.POST.get('order_id')
    new_status = request.POST.get('status')
    order = get_object_or_404(Order, id=order_id)
    
    if new_status in dict(Order.STATUS_CHOICES):
        old_status = order.status
        order.status = new_status
        order.save()
        
        # If order is cancelled, restore stock
        if new_status == 'cancelled' and old_status != 'cancelled':
            for item in order.items.all():
                item.price_slab.restore_stock(item.quantity)
        # If order was cancelled and is now restored, subtract stock again
        elif old_status == 'cancelled' and new_status != 'cancelled':
            for item in order.items.all():
                item.price_slab.deduct_stock(item.quantity)
        
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
    # Try sending via Email SMTP if SmtpConfig is set up
    try:
        from shop.models import SmtpConfig
        config = SmtpConfig.objects.first()
        if config and config.smtp_user and config.recipient_email:
            from django.core.mail import EmailMessage, get_connection
            use_ssl = (config.smtp_port == 465)
            use_tls = (config.smtp_port == 587 or getattr(config, 'use_tls', True)) if not use_ssl else False
            
            class IPv4OnlySMTP:
                def __enter__(self):
                    import socket
                    self.old_getaddrinfo = socket.getaddrinfo
                    def getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
                        return self.old_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
                    socket.getaddrinfo = getaddrinfo_ipv4
                    return self
                def __exit__(self, exc_type, exc_val, exc_tb):
                    import socket
                    socket.getaddrinfo = self.old_getaddrinfo

            with IPv4OnlySMTP():
                conn = get_connection(
                    host=config.smtp_host,
                    port=config.smtp_port,
                    username=config.smtp_user,
                    password=config.smtp_password,
                    use_tls=use_tls,
                    use_ssl=use_ssl,
                    timeout=10
                )
                recipients = [e.strip() for e in config.recipient_email.split(',') if e.strip()]
                msg = EmailMessage(
                    subject=f"🔑 SMK Flour Shop Customer Login OTP: {otp}",
                    body=f"Hello,\n\nA customer with mobile number {phone_number} requested a login verification code.\n\n4-Digit OTP Code: {otp}\n\nBest regards,\nSMK Flour Shop System",
                    from_email=config.smtp_user,
                    to=recipients,
                    connection=conn
                )
                msg.send(fail_silently=True)
                print(f"[OTP SERVICE] Sent Email OTP {otp} for {phone_number} to {recipients}", file=sys.stderr)
    except Exception as e:
        print(f"[OTP SERVICE] Email OTP dispatch error: {e}", file=sys.stderr)

    sms_provider = os.getenv('SMS_PROVIDER', 'none').lower().strip()
    if sms_provider == 'none' and os.getenv('TWILIO_ACCOUNT_SID'):
        sms_provider = 'twilio'
    elif sms_provider == 'none' and os.getenv('FAST2SMS_API_KEY'):
        sms_provider = 'fast2sms'
    
    business_name = os.getenv('SMS_BUSINESS_NAME', 'Manikandan Maavu Kadai').strip()
    message = f"Your {business_name} verification code is {otp}."
    
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
        print("DEBUG SMS: OTP logged securely.", file=sys.stderr)
        return True

import random

def customer_login(request):
    if request.method == 'POST':
        raw_phone = request.POST.get('phone_number', '').strip()
        import re
        phone_number = re.sub(r'\D', '', raw_phone)
        if len(phone_number) > 10:
            phone_number = phone_number[-10:]
            
        # Strict Indian Mobile Number Validation: 10 digits starting with 6, 7, 8, 9
        if not phone_number or not re.match(r'^[6-9]\d{9}$', phone_number):
            return render(request, 'shop/login.html', {'error': 'Please enter a valid 10-digit mobile number starting with 6, 7, 8, or 9.'})
        
        otp = str(random.randint(1000, 9999))
        request.session['login_otp'] = otp
        request.session['login_phone'] = phone_number
        
        # Save OTP log to DB for Admin Support Desk
        from shop.models import CustomerOtpLog
        CustomerOtpLog.objects.create(phone_number=phone_number, otp_code=otp)

        # Send SMS / Email OTP asynchronously in background thread for instant response
        import threading
        t = threading.Thread(target=send_sms_otp, args=(phone_number, otp), daemon=True)
        t.start()
        
        # Log OTP securely to server logs
        print("\n" + "="*50)
        print(f"[OTP SERVICE] Verification code for {phone_number} is: {otp}")
        print("="*50 + "\n")
        
        from shop.models import WhatsAppConfig
        whatsapp_config = WhatsAppConfig.get_config()
        formatted_message = whatsapp_config.get_formatted_message(otp)

        import urllib.parse
        encoded_message = urllib.parse.quote(formatted_message)
        whatsapp_url = f"https://wa.me/91{whatsapp_config.whatsapp_number}?text={encoded_message}"

        return render(request, 'shop/login.html', {
            'phone_number': phone_number,
            'otp_sent': True,
            'otp': otp,
            'whatsapp_config': whatsapp_config,
            'whatsapp_message': formatted_message,
            'whatsapp_url': whatsapp_url,
            'auto_open_whatsapp': True
        })
        
    from shop.models import WhatsAppConfig
    whatsapp_config = WhatsAppConfig.get_config()
    return render(request, 'shop/login.html', {'whatsapp_config': whatsapp_config})

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
            
            # Mark OTP log as verified
            from shop.models import CustomerOtpLog
            CustomerOtpLog.objects.filter(phone_number=phone_number, otp_code=user_otp).update(is_verified=True)

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

from io import BytesIO
from django.http import FileResponse
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

def download_invoice(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    items = order.items.select_related('product', 'price_slab').all()
    
    # Check authorization: either staff, or the customer who placed the order
    is_staff = request.user.is_staff
    is_owner = False
    customer_id = request.session.get('customer_id')
    if customer_id and order.customer_id == customer_id:
        is_owner = True
        
    if not (is_staff or is_owner):
        return HttpResponse("Unauthorized to view this invoice", status=403)
        
    # Create the PDF buffer
    buffer = BytesIO()
    
    # Setup document template
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )
    
    styles = getSampleStyleSheet()
    
    # Custom Styles matching the clean/premium aesthetic
    body_bold = ParagraphStyle(
        'BodyBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#1d1d1f')
    )
    
    body_style = ParagraphStyle(
        'BodyNormal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#1d1d1f')
    )
    
    right_align = ParagraphStyle(
        'RightAlign',
        parent=body_style,
        alignment=2 # Right align
    )
    
    right_align_bold = ParagraphStyle(
        'RightAlignBold',
        parent=body_bold,
        alignment=2
    )

    story = []
    
    # 1. Header Information (Two Column layout: Company Details vs Invoice Details)
    # Query shop details dynamically from the database
    shop = Shop.objects.first()
    if shop:
        shop_name = shop.name
        shop_address = shop.address.replace('\n', '<br/>')
        shop_phone = shop.contact_number
    else:
        # Fallback values if no Shop is configured in the database
        shop_name = "SMK Flour Shop"
        shop_address = "Opposite Rani Hospital,<br/>Selvapuram, Coimbatore - 641026"
        shop_phone = "+91 7397536217"

    company_info = f"""
    <b>{shop_name}</b><br/>
    {shop_address}<br/>
    Phone: {shop_phone}
    """
    
    invoice_details = f"""
    <b>INVOICE RECEIPT</b><br/><br/>
    <b>Invoice No:</b> #{order.id}<br/>
    <b>Date:</b> {order.created_at.strftime('%d-%b-%Y %I:%M %p')}<br/>
    <b>Order Type:</b> {order.get_order_type_display()}<br/>
    <b>Status:</b> {order.get_status_display().upper()}<br/>
    """
    
    header_data = [
        [Paragraph(company_info, body_style), Paragraph(invoice_details, right_align)]
    ]
    
    header_table = Table(header_data, colWidths=[3.5*inch, 3.5*inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('PADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 15))
    
    # Horizontal line
    line_table = Table([['']], colWidths=[7.0*inch])
    line_table.setStyle(TableStyle([
        ('LINEBELOW', (0,0), (-1,-1), 1, colors.HexColor('#e8e8ed')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(line_table)
    story.append(Spacer(1, 15))
    
    # 2. Customer details & Address
    cust_name = order.customer.name if order.customer.name else 'Valued Customer'
    cust_info = f"""
    <b>Bill To:</b><br/>
    {cust_name}<br/>
    Phone: {order.customer.phone_number}
    """
    
    address_info = "<b>Delivery Address:</b><br/>Store Pickup"
    if order.order_type == 'delivery' and order.address:
        address_info = f"""
        <b>Delivery Address:</b><br/>
        {order.address.address_text}<br/>
        Landmark: {order.address.landmark or 'N/A'}
        """
        
    cust_table_data = [
        [Paragraph(cust_info, body_style), Paragraph(address_info, body_style)]
    ]
    cust_table = Table(cust_table_data, colWidths=[3.5*inch, 3.5*inch])
    cust_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('PADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(cust_table)
    story.append(Spacer(1, 20))
    
    # 3. Items Table
    table_data = [
        [
            Paragraph('<b>Product Description</b>', body_bold),
            Paragraph('<b>Slab Size</b>', body_bold),
            Paragraph('<b>Price</b>', right_align_bold),
            Paragraph('<b>Qty</b>', right_align_bold),
            Paragraph('<b>Total</b>', right_align_bold)
        ]
    ]
    
    for item in items:
        table_data.append([
            Paragraph(item.product.name_en, body_style),
            Paragraph(f"{item.price_slab.quantity_value} {item.price_slab.get_quantity_unit_display()}", body_style),
            Paragraph(f"Rs. {item.price_slab.price:.2f}", right_align),
            Paragraph(str(item.quantity), right_align),
            Paragraph(f"Rs. {item.subtotal:.2f}", right_align)
        ])
        
    items_table = Table(table_data, colWidths=[2.6*inch, 1.2*inch, 1.1*inch, 0.8*inch, 1.3*inch])
    items_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f5f5f7')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor('#e8e8ed')),
        ('LINEBELOW', (0,0), (-1,0), 1.5, colors.HexColor('#1d1d1f')),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 15))
    
    # 4. Summary & Payments Table
    subtotal = float(order.total_amount) - float(order.delivery_fee)
    summary_rows = [
        [Paragraph('<b>Items Subtotal:</b>', right_align), Paragraph(f"Rs. {subtotal:.2f}", right_align)],
        [Paragraph('<b>Delivery Fee:</b>', right_align), Paragraph(f"Rs. {order.delivery_fee:.2f}", right_align)],
        [Paragraph('<b>Grand Total:</b>', right_align_bold), Paragraph(f"Rs. {order.total_amount:.2f}", right_align_bold)]
    ]
    
    summary_table = Table(summary_rows, colWidths=[5.5*inch, 1.5*inch])
    summary_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 4),
        ('LINEABOVE', (0,-1), (-1,-1), 1, colors.HexColor('#1d1d1f')),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 30))
    
    # 5. Payment details & Footer Thank You
    try:
        payment = order.payment
        pay_method = 'Online Payment' if payment.method == 'online' else 'Cash on Delivery'
        pay_status = payment.status.upper()
    except Exception:
        pay_method = 'N/A'
        pay_status = 'PENDING'
        
    pay_info = f"""
    <b>Payment Method:</b> {pay_method}<br/>
    <b>Payment Status:</b> <font color="{'green' if pay_status == 'COMPLETED' else 'red'}">{pay_status}</font>
    """
    
    thank_you_text = """
    <b>Thank you for your order!</b><br/>
    We appreciate your business. Batter fresh, eat fresh!
    """
    
    footer_data = [
        [Paragraph(pay_info, body_style), Paragraph(thank_you_text, right_align)]
    ]
    footer_table = Table(footer_data, colWidths=[3.5*inch, 3.5*inch])
    footer_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('PADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(footer_table)
    
    # Build document
    doc.build(story)
    
    # Get the value of the BytesIO buffer and write it to the response.
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=f"invoice_order_{order.id}.pdf")

from django.contrib.auth import login as auth_login, authenticate
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import PermissionDenied

def delivery_required(view_func):
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('delivery_login')
        # Check if user is staff, superuser, or in Delivery Staff group
        in_group = request.user.groups.filter(name='Delivery Staff').exists()
        if request.user.is_staff or request.user.is_superuser or in_group:
            return view_func(request, *args, **kwargs)
        raise PermissionDenied("You do not have access to the delivery dashboard.")
    return _wrapped_view

def delivery_login(request):
    if request.user.is_authenticated:
        in_group = request.user.groups.filter(name='Delivery Staff').exists()
        if request.user.is_staff or request.user.is_superuser or in_group:
            return redirect('delivery_dashboard')
            
    error_msg = None
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            in_group = user.groups.filter(name='Delivery Staff').exists()
            if user.is_staff or user.is_superuser or in_group:
                auth_login(request, user)
                return redirect('delivery_dashboard')
            else:
                error_msg = "Your account is not registered as delivery staff."
        else:
            error_msg = "Invalid username or password."
    else:
        form = AuthenticationForm()
        
    return render(request, 'shop/delivery_login.html', {
        'form': form,
        'error_msg': error_msg
    })

@delivery_required
def delivery_dashboard(request):
    # Assigned deliveries (status ready, preparing, received)
    assigned_orders = Order.objects.filter(
        assigned_delivery=request.user,
        order_type='delivery',
        status__in=['received', 'preparing', 'ready']
    ).select_related('customer', 'address', 'payment').order_by('-created_at')
    
    # Completed deliveries (past deliveries)
    completed_orders = Order.objects.filter(
        assigned_delivery=request.user,
        order_type='delivery',
        status='completed'
    ).select_related('customer', 'address', 'payment').order_by('-updated_at')[:20]
    
    return render(request, 'shop/delivery_dashboard.html', {
        'assigned_orders': assigned_orders,
        'completed_orders': completed_orders
    })

@delivery_required
@require_POST
def delivery_mark_completed(request):
    order_id = request.POST.get('order_id')
    order = get_object_or_404(Order, id=order_id, assigned_delivery=request.user)
    
    # Mark order status completed
    order.status = 'completed'
    order.save()
    
    # If payment is COD, mark payment status completed
    try:
        payment = order.payment
        if payment.method == 'cod' and payment.status != 'completed':
            payment.status = 'completed'
            payment.paid_at = timezone.now()
            payment.save()
    except Payment.DoesNotExist:
        pass
        
    if request.htmx:
        response = HttpResponse("")
        response['HX-Trigger'] = 'deliveryStatusUpdated'
        return response
        
    return redirect('delivery_dashboard')

def get_user_phone(user):
    for val in [user.username, user.first_name, user.email]:
        clean = "".join(filter(str.isdigit, val))
        if len(clean) >= 10:
            return clean[-10:]
    return None

def send_custom_sms(phone_number, message):
    import urllib.request
    import urllib.parse
    import base64
    import json
    import sys
    import os
    
    sms_provider = os.getenv('SMS_PROVIDER', 'none').lower().strip()
    if sms_provider == 'none' and os.getenv('TWILIO_ACCOUNT_SID'):
        sms_provider = 'twilio'
    elif sms_provider == 'none' and os.getenv('FAST2SMS_API_KEY'):
        sms_provider = 'fast2sms'
    
    formatted_phone = phone_number
    if len(phone_number) == 10 and phone_number.isdigit():
        formatted_phone = "+91" + phone_number

    print(f"DEBUG SMS: Attempting to send custom SMS to {phone_number} using {sms_provider}", file=sys.stderr)

    if sms_provider == 'twilio':
        account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        from_phone = os.getenv('TWILIO_PHONE_NUMBER')
        if not account_sid or not auth_token or not from_phone:
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
                return True
        except Exception as e:
            print("ERROR SMS: Twilio custom SMS failed:", str(e), file=sys.stderr)
            return False

    elif sms_provider == 'fast2sms':
        api_key = os.getenv('FAST2SMS_API_KEY')
        if not api_key:
            return False
            
        raw_10_digits = phone_number[-10:]
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
                print("DEBUG SMS: Fast2SMS custom SMS response:", res_json, file=sys.stderr)
                return res_json.get('return', False)
        except Exception as e:
            print("ERROR SMS: Fast2SMS custom SMS failed:", str(e), file=sys.stderr)
            return False
    return False

@staff_member_required
@require_POST
def admin_assign_delivery(request):
    from django.contrib.auth.models import User
    order_id = request.POST.get('order_id')
    agent_id = request.POST.get('agent_id')
    order = get_object_or_404(Order, id=order_id)
    
    if agent_id:
        agent = get_object_or_404(User, id=agent_id)
        order.assigned_delivery = agent
        order.save()
        
        # Send notification to the delivery agent
        phone = get_user_phone(agent)
        if phone:
            business_name = os.getenv('SMS_BUSINESS_NAME', 'Manikandan Maavu Kadai').strip()
            msg = f"New order #{order.id} assigned to you by {business_name}. Customer: {order.customer.name or order.customer.phone_number}, Landmark: {order.address.landmark if order.address else 'N/A'}. Please check your delivery dashboard."
            send_custom_sms(phone, msg)
    else:
        order.assigned_delivery = None
        order.save()
    
    if request.htmx:
        response = HttpResponse("")
        response['HX-Trigger'] = 'newOrderReceived'
        return response
        
    return redirect('admin_dashboard')

@staff_member_required
@require_POST
def admin_update_inventory(request):
    from decimal import Decimal
    target_type = request.POST.get('target_type')
    
    if target_type == 'bulk':
        product_id = request.POST.get('product_id')
        product = get_object_or_404(Product, id=product_id)
        raw_val = request.POST.get('value')
        if raw_val is None:
            raw_val = request.POST.get(f'bulk_stock_{product_id}', '0')
        try:
            product.bulk_stock = Decimal(raw_val)
            product.save(update_fields=['bulk_stock'])
        except Exception:
            pass
    elif target_type == 'packaged':
        slab_id = request.POST.get('slab_id')
        slab = get_object_or_404(PriceSlab, id=slab_id)
        raw_val = request.POST.get('value')
        if raw_val is None:
            raw_val = request.POST.get(f'slab_stock_{slab_id}', '0')
        try:
            slab.stock = int(Decimal(raw_val))
            slab.save(update_fields=['stock'])
        except Exception:
            pass
    elif target_type == 'product_config':
        product_id = request.POST.get('product_id')
        product = get_object_or_404(Product, id=product_id)
        inventory_type = request.POST.get(f'inventory_type_{product_id}') or request.POST.get('inventory_type')
        bulk_unit = request.POST.get('bulk_unit')
        if inventory_type in ['bulk', 'packaged']:
            product.inventory_type = inventory_type
        if bulk_unit in ['kg', 'g', 'l', 'ml', 'piece']:
            product.bulk_unit = bulk_unit
        product.save()
    elif target_type == 'image_url':
        product_id = request.POST.get('product_id')
        product = get_object_or_404(Product, id=product_id)
        url_val = request.POST.get(f'image_url_{product_id}', '').strip()
        product.image_url = url_val if url_val else None
        product.save(update_fields=['image_url'])
        
    if getattr(request, 'htmx', False) or request.headers.get('HX-Request'):
        response = HttpResponse("")
        response['HX-Refresh'] = 'true'
        return response
        
    return redirect('admin_dashboard')

@staff_member_required
@require_POST
def admin_bulk_update_inventory(request):
    from decimal import Decimal
    for key, val in request.POST.items():
        if key.startswith('bulk_stock_'):
            try:
                product_id = key.replace('bulk_stock_', '')
                product = Product.objects.get(id=product_id)
                product.bulk_stock = Decimal(val)
                product.save(update_fields=['bulk_stock'])
            except Exception:
                pass
        elif key.startswith('slab_stock_'):
            try:
                slab_id = key.replace('slab_stock_', '')
                slab = PriceSlab.objects.get(id=slab_id)
                slab.stock = int(val)
                slab.save(update_fields=['stock'])
            except Exception:
                pass

    if request.htmx:
        response = HttpResponse("")
        response['HX-Trigger'] = 'inventoryUpdated'
        return response

    return redirect('admin_dashboard')

@staff_member_required
@require_POST
def admin_create_product(request):
    from decimal import Decimal
    
    category_id = request.POST.get('category_id')
    name_en = request.POST.get('name_en')
    name_ta = request.POST.get('name_ta')
    description_en = request.POST.get('description_en', '')
    description_ta = request.POST.get('description_ta', '')
    image_file = request.FILES.get('image')
    image_url = request.POST.get('image_url', '').strip()
    
    inventory_type = request.POST.get('inventory_type', 'packaged')
    bulk_stock_val = request.POST.get('bulk_stock', '100.00')
    bulk_unit = request.POST.get('bulk_unit', 'kg')
    
    category = get_object_or_404(Category, id=category_id)
    
    product = Product.objects.create(
        category=category,
        name_en=name_en,
        name_ta=name_ta,
        description_en=description_en,
        description_ta=description_ta,
        image=image_file,
        image_url=image_url if image_url else None,
        inventory_type=inventory_type,
        bulk_stock=Decimal(bulk_stock_val),
        bulk_unit=bulk_unit
    )
    
    price_val = request.POST.get('slab_price')
    qty_val = request.POST.get('slab_qty_value')
    qty_unit = request.POST.get('slab_qty_unit')
    slab_stock_val = request.POST.get('slab_stock', '100')
    
    if price_val and qty_val and qty_unit:
        PriceSlab.objects.create(
            product=product,
            price=Decimal(price_val),
            quantity_value=Decimal(qty_val),
            quantity_unit=qty_unit,
            stock=int(slab_stock_val)
        )
        
    if request.htmx:
        response = HttpResponse("")
        response['HX-Trigger'] = 'inventoryUpdated'
        return response
        
    return redirect('admin_dashboard')

@staff_member_required
@require_POST
def admin_add_slab(request):
    from decimal import Decimal
    product_id = request.POST.get('product_id')
    product = get_object_or_404(Product, id=product_id)
    
    price_val = request.POST.get('price')
    qty_val = request.POST.get('quantity_value')
    qty_unit = request.POST.get('quantity_unit')
    stock_val = request.POST.get('stock', '100')
    
    if price_val and qty_val and qty_unit:
        PriceSlab.objects.create(
            product=product,
            price=Decimal(price_val),
            quantity_value=Decimal(qty_val),
            quantity_unit=qty_unit,
            stock=int(stock_val)
        )
        
    if request.htmx:
        response = HttpResponse("")
        response['HX-Trigger'] = 'inventoryUpdated'
        return response
        
    return redirect('admin_dashboard')

@staff_member_required
@require_POST
def admin_clear_inventory(request):
    Product.objects.update(bulk_stock=0.00)
    PriceSlab.objects.update(stock=0)
    
    if request.htmx:
        response = HttpResponse("")
        response['HX-Trigger'] = 'inventoryUpdated'
        return response
        
    return redirect('admin_dashboard')

@staff_member_required
def admin_generate_report(request):
    from shop.models import Order, Payment, Product, Category, OrderItem, SmtpConfig
    from django.db.models import Sum, Count
    from django.utils import timezone
    from datetime import timedelta
    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch

    date_range = request.POST.get('date_range', request.GET.get('date_range', 'all'))
    report_format = request.POST.get('format', request.GET.get('format', 'pdf'))
    
    include_orders = request.POST.get('include_orders') == 'true' or request.GET.get('include_orders') == 'true' or 'include_orders' in request.POST
    include_payments = request.POST.get('include_payments') == 'true' or request.GET.get('include_payments') == 'true' or 'include_payments' in request.POST
    include_inventory = request.POST.get('include_inventory') == 'true' or request.GET.get('include_inventory') == 'true' or 'include_inventory' in request.POST
    include_analytics = request.POST.get('include_analytics') == 'true' or request.GET.get('include_analytics') == 'true' or 'include_analytics' in request.POST

    if not (include_orders or include_payments or include_inventory or include_analytics):
        include_orders = include_payments = include_inventory = include_analytics = True

    now = timezone.localtime(timezone.now())
    if date_range == 'today':
        start_date = now.date()
        orders_qs = Order.objects.filter(created_at__date=start_date)
        payments_qs = Payment.objects.filter(created_at__date=start_date)
    elif date_range == 'week':
        start_date = (now - timedelta(days=7)).date()
        orders_qs = Order.objects.filter(created_at__date__gte=start_date)
        payments_qs = Payment.objects.filter(created_at__date__gte=start_date)
    elif date_range == 'month':
        start_date = (now - timedelta(days=30)).date()
        orders_qs = Order.objects.filter(created_at__date__gte=start_date)
        payments_qs = Payment.objects.filter(created_at__date__gte=start_date)
    else:
        orders_qs = Order.objects.all()
        payments_qs = Payment.objects.all()

    orders_list = list(orders_qs.select_related('customer').order_by('-created_at'))
    payments_list = list(payments_qs.select_related('order', 'order__customer').order_by('-created_at'))
    products_list = list(Product.objects.all().prefetch_related('price_slabs').select_related('category').order_by('category__display_order', 'name_en'))
    
    total_rev = payments_qs.filter(status='completed').aggregate(total=Sum('amount'))['total'] or 0.0
    top_prods = list(OrderItem.objects.values('product__name_en', 'product__name_ta').annotate(total_qty=Sum('quantity')).order_by('-total_qty')[:5])

    if report_format == 'pdf':
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
        styles = getSampleStyleSheet()
        
        title_style = ParagraphStyle('ReportTitle', parent=styles['Heading1'], fontSize=16, leading=20, textColor=colors.HexColor('#1d1d1f'), alignment=1)
        sub_style = ParagraphStyle('ReportSub', parent=styles['Normal'], fontSize=9, leading=12, textColor=colors.HexColor('#86868b'), alignment=1)
        h2_style = ParagraphStyle('SectionHeader', parent=styles['Heading2'], fontSize=11, leading=15, textColor=colors.HexColor('#0071e3'), spaceBefore=10, spaceAfter=4)
        cell_style = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=8, leading=10)
        cell_bold = ParagraphStyle('CellBold', parent=styles['Normal'], fontSize=8, leading=10, fontName='Helvetica-Bold')

        elements = []
        elements.append(Paragraph("<b>SMK FLOUR SHOP — OPERATIONS & BUSINESS REPORT</b>", title_style))
        elements.append(Paragraph(f"Generated on: {now.strftime('%d %b %Y, %I:%M %p')} | Range: {date_range.upper()}", sub_style))
        elements.append(Spacer(1, 10))

        if include_analytics:
            elements.append(Paragraph("1. Executive Analytics Summary", h2_style))
            summary_data = [
                [Paragraph("Total Revenue", cell_bold), Paragraph(f"₹{total_rev:,.2f}", cell_bold),
                 Paragraph("Total Orders Placed", cell_bold), Paragraph(str(len(orders_list)), cell_bold)]
            ]
            t_sum = Table(summary_data, colWidths=[1.5*inch, 2*inch, 1.5*inch, 2*inch])
            t_sum.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f5f5f7')),
                ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#e8e8ed')),
                ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e8e8ed')),
                ('PADDING', (0,0), (-1,-1), 5),
            ]))
            elements.append(t_sum)
            elements.append(Spacer(1, 8))

            if top_prods:
                elements.append(Paragraph("<b>Top Selling Products</b>", ParagraphStyle('SubSub', parent=styles['Normal'], fontSize=8, leading=10, fontName='Helvetica-Bold')))
                top_data = [[Paragraph("Product Name", cell_bold), Paragraph("Units Sold", cell_bold)]]
                for tp in top_prods:
                    top_data.append([Paragraph(tp['product__name_en'], cell_style), Paragraph(str(tp['total_qty']), cell_style)])
                t_top = Table(top_data, colWidths=[4.5*inch, 2.5*inch])
                t_top.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e8e8ed')),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e8e8ed')),
                    ('PADDING', (0,0), (-1,-1), 4),
                ]))
                elements.append(t_top)
                elements.append(Spacer(1, 10))

        if include_orders:
            elements.append(Paragraph("2. Live Orders & Fulfillment Register", h2_style))
            ord_data = [[Paragraph("Order ID", cell_bold), Paragraph("Customer", cell_bold), Paragraph("Phone", cell_bold), Paragraph("Type", cell_bold), Paragraph("Status", cell_bold), Paragraph("Total", cell_bold)]]
            for o in orders_list[:30]:
                c_name = o.customer.name if o.customer else "Guest"
                c_phone = o.customer.phone_number if o.customer else "-"
                ord_data.append([
                    Paragraph(f"#{o.id}", cell_style),
                    Paragraph(c_name, cell_style),
                    Paragraph(c_phone, cell_style),
                    Paragraph(o.get_order_type_display(), cell_style),
                    Paragraph(o.get_status_display(), cell_style),
                    Paragraph(f"₹{o.total_amount:,.2f}", cell_style)
                ])
            t_ord = Table(ord_data, colWidths=[0.8*inch, 1.8*inch, 1.2*inch, 1*inch, 1.2*inch, 1*inch])
            t_ord.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e8e8ed')),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e8e8ed')),
                ('PADDING', (0,0), (-1,-1), 4),
            ]))
            elements.append(t_ord)
            elements.append(Spacer(1, 10))

        if include_payments:
            elements.append(Paragraph("3. Payment Reconciliation Ledger", h2_style))
            pay_data = [[Paragraph("Order ID", cell_bold), Paragraph("Method", cell_bold), Paragraph("Gateway Txn ID", cell_bold), Paragraph("Amount", cell_bold), Paragraph("Status", cell_bold)]]
            for p in payments_list[:30]:
                pay_data.append([
                    Paragraph(f"#{p.order.id}", cell_style),
                    Paragraph(p.get_method_display(), cell_style),
                    Paragraph(p.gateway_txn_id or "N/A (COD)", cell_style),
                    Paragraph(f"₹{p.amount:,.2f}", cell_style),
                    Paragraph(p.get_status_display(), cell_style)
                ])
            t_pay = Table(pay_data, colWidths=[0.8*inch, 1.5*inch, 2.2*inch, 1.2*inch, 1.3*inch])
            t_pay.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e8e8ed')),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e8e8ed')),
                ('PADDING', (0,0), (-1,-1), 4),
            ]))
            elements.append(t_pay)
            elements.append(Spacer(1, 10))

        if include_inventory:
            elements.append(Paragraph("4. Inventory Stock & Product Audit", h2_style))
            inv_data = [[Paragraph("Product", cell_bold), Paragraph("Category", cell_bold), Paragraph("Type", cell_bold), Paragraph("Stock Level", cell_bold)]]
            for pr in products_list:
                if pr.inventory_type == 'bulk':
                    st_str = f"{pr.bulk_stock} {pr.bulk_unit}"
                else:
                    sl_strs = [f"{s.quantity_value}{s.get_quantity_unit_display()}: {s.stock}p" for s in pr.price_slabs.all()]
                    st_str = ", ".join(sl_strs) if sl_strs else "No slabs"
                inv_data.append([
                    Paragraph(pr.name_en, cell_style),
                    Paragraph(pr.category.name_en if pr.category else "-", cell_style),
                    Paragraph(pr.inventory_type.upper(), cell_style),
                    Paragraph(st_str, cell_style)
                ])
            t_inv = Table(inv_data, colWidths=[2.2*inch, 1.5*inch, 1*inch, 2.3*inch])
            t_inv.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e8e8ed')),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e8e8ed')),
                ('PADDING', (0,0), (-1,-1), 4),
            ]))
            elements.append(t_inv)

        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()

        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="smk_flour_shop_report_{now.strftime("%Y%m%d")}.pdf"'
        return response

    else:
        context = {
            'now': now,
            'date_range': date_range,
            'include_orders': include_orders,
            'include_payments': include_payments,
            'include_inventory': include_inventory,
            'include_analytics': include_analytics,
            'total_rev': total_rev,
            'orders_list': orders_list,
            'payments_list': payments_list,
            'products_list': products_list,
            'top_prods': top_prods,
        }
        return render(request, 'shop/report_print.html', context)

@staff_member_required
@require_POST
def admin_update_smtp_config(request):
    from shop.models import SmtpConfig
    action = request.POST.get('action', 'save')
    
    try:
        config = SmtpConfig.objects.first()
        if not config:
            config = SmtpConfig()
        
        if action == 'clear':
            config.smtp_user = ''
            config.smtp_password = ''
            config.recipient_email = ''
            config.auto_daily_email = False
            config.save()
            msg = '<div style="padding: 0.8rem; background-color: #fde8e8; border: 1px solid #f8b4b4; color: #9b1c1c; border-radius: 8px; font-weight: 600; margin-bottom: 1rem; font-size: 0.85rem;"><i class="fa-solid fa-trash-can"></i> SMTP Configuration Reset & Cleared Successfully!</div>'
        else:
            config.smtp_host = request.POST.get('smtp_host', 'smtp.gmail.com').strip()
            config.smtp_port = int(request.POST.get('smtp_port', 587))
            config.smtp_user = request.POST.get('smtp_user', '').strip()
            config.smtp_password = request.POST.get('smtp_password', '').strip()
            config.recipient_email = request.POST.get('recipient_email', '').strip()
            config.auto_daily_email = request.POST.get('auto_daily_email') == 'true' or 'auto_daily_email' in request.POST
            config.save()
            msg = '<div style="padding: 0.8rem; background-color: #def7ec; border: 1px solid #84e1bc; color: #03543f; border-radius: 8px; font-weight: 600; margin-bottom: 1rem; font-size: 0.85rem;"><i class="fa-solid fa-circle-check"></i> SMTP Configuration Saved Successfully!</div>'
            
    except Exception as e:
        msg = f'<div style="padding: 0.8rem; background-color: #fde8e8; border: 1px solid #f8b4b4; color: #9b1c1c; border-radius: 8px; font-weight: 600; margin-bottom: 1rem; font-size: 0.85rem;"><i class="fa-solid fa-triangle-exclamation"></i> Error saving configuration: {str(e)}</div>'
        if getattr(request, 'htmx', False) or request.headers.get('HX-Request'):
            return HttpResponse(msg, status=200)
        return redirect('admin_dashboard')
    
    if getattr(request, 'htmx', False) or request.headers.get('HX-Request'):
        response = HttpResponse(msg)
        response['HX-Trigger'] = 'smtpConfigUpdated'
        return response
    return redirect('admin_dashboard')

@staff_member_required
@require_POST
def admin_send_test_email_report(request):
    from shop.models import SmtpConfig, Order, Payment, Product
    from django.core.mail import EmailMultiAlternatives, get_connection
    from django.db.models import Sum
    from django.utils import timezone

    try:
        config = SmtpConfig.objects.first()
        if not config:
            config = SmtpConfig()

        post_host = request.POST.get('smtp_host', '').strip()
        post_port = request.POST.get('smtp_port', '').strip()
        post_user = request.POST.get('smtp_user', '').strip()
        post_pass = request.POST.get('smtp_password', '').strip()
        post_recip = request.POST.get('recipient_email', '').strip()

        smtp_host = post_host or config.smtp_host or 'smtp.gmail.com'
        try:
            smtp_port = int(post_port) if post_port else (config.smtp_port or 587)
        except ValueError:
            smtp_port = 587
        smtp_user = post_user or config.smtp_user or ''
        smtp_password = post_pass or config.smtp_password or ''
        recipient_email = post_recip or config.recipient_email or ''

        if not smtp_user or not recipient_email:
            return HttpResponse('<div style="padding: 0.8rem; background-color: #feecdc; border: 1px solid #fbd5a5; color: #b43403; border-radius: 8px; font-weight: 600; margin-bottom: 1rem; font-size: 0.85rem;"><i class="fa-solid fa-triangle-exclamation"></i> Please enter your SMTP Username and Recipient Email address first!</div>', status=200)
        
        config.smtp_host = smtp_host
        config.smtp_port = smtp_port
        config.smtp_user = smtp_user
        if smtp_password:
            config.smtp_password = smtp_password
        config.recipient_email = recipient_email
        config.save()

        from django.test import RequestFactory
        req = RequestFactory().get('/admin-dashboard/generate-report/?format=pdf')
        req.user = request.user
        pdf_response = admin_generate_report(req)
        pdf_data = pdf_response.content
        
        use_ssl = (smtp_port == 465)
        use_tls = (smtp_port == 587 or getattr(config, 'use_tls', True)) if not use_ssl else False
        
        class IPv4OnlySMTP:
            def __enter__(self):
                import socket
                self.old_getaddrinfo = socket.getaddrinfo
                def getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
                    return self.old_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
                socket.getaddrinfo = getaddrinfo_ipv4
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                import socket
                socket.getaddrinfo = self.old_getaddrinfo

        recipients = [e.strip() for e in recipient_email.split(',') if e.strip()]
        
        now_str = timezone.localtime(timezone.now()).strftime('%d %b %Y, %I:%M %p')
        today_date = timezone.localtime(timezone.now()).date()
        total_rev = Payment.objects.filter(status='completed').aggregate(total=Sum('amount'))['total'] or 0.0
        total_orders = Order.objects.count()
        today_orders = Order.objects.filter(created_at__date=today_date).count()
        pending_deliveries = Order.objects.filter(order_type='delivery', status__in=['received', 'preparing', 'ready']).count()
        low_stock_count = Product.objects.filter(inventory_type='bulk', bulk_stock__lt=10.0).count()
        site_url = getattr(settings, 'SITE_URL', 'https://smk-flour-shop.onrender.com')

        text_content = f"SMK Flour Shop — Operations & Business Report\nGenerated: {now_str}\nTotal Revenue: ₹{total_rev:,.2f}\nTotal Orders: {total_orders}\n\nPlease view the attached PDF."
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #fdfbf7; margin: 0; padding: 20px; color: #2c2420; }}
                .email-container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 16px; border: 1px solid #e2dad0; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }}
                .email-header {{ background: linear-gradient(135deg, #2e7d32 0%, #1b5e20 100%); padding: 25px 30px; text-align: center; color: #ffffff; }}
                .email-header h1 {{ margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0.5px; }}
                .email-header p {{ margin: 5px 0 0 0; font-size: 13px; opacity: 0.9; }}
                .email-body {{ padding: 30px; }}
                .metric-grid {{ display: table; width: 100%; margin-bottom: 25px; border-spacing: 10px; }}
                .metric-cell {{ display: table-cell; width: 50%; background-color: #f7f3ed; padding: 15px; border-radius: 12px; border: 1px solid #e8e0d5; text-align: center; }}
                .metric-label {{ font-size: 11px; font-weight: 700; text-transform: uppercase; color: #7a6e65; letter-spacing: 0.5px; }}
                .metric-val {{ font-size: 22px; font-weight: 800; color: #2e7d32; margin-top: 5px; }}
                .section-header {{ font-size: 15px; font-weight: 700; color: #8d2f00; margin-bottom: 12px; border-bottom: 2px solid #f2e9de; padding-bottom: 6px; }}
                .detail-table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 13px; }}
                .detail-table td {{ padding: 10px 12px; border-bottom: 1px solid #f2e9de; }}
                .btn-cta {{ display: inline-block; background-color: #8d2f00; color: #ffffff !important; padding: 12px 25px; border-radius: 8px; font-weight: 700; text-decoration: none; font-size: 14px; margin-top: 15px; }}
                .email-footer {{ background-color: #f7f3ed; padding: 20px; text-align: center; font-size: 12px; color: #7a6e65; border-top: 1px solid #e8e0d5; }}
            </style>
        </head>
        <body>
            <div class="email-container">
                <div class="email-header">
                    <h1>🌾 SMK MAAVU KADAI</h1>
                    <p>Daily Business & Operations Executive Summary</p>
                </div>
                <div class="email-body">
                    <p style="font-size: 14px; margin-top: 0;">Hello Management,</p>
                    <p style="font-size: 13px; color: #5a5048;">Here is your automated business performance snapshot generated on <strong>{now_str}</strong>. The complete PDF report is also attached to this email.</p>
                    
                    <div class="metric-grid">
                        <div class="metric-cell">
                            <div class="metric-label">Total Revenue Collected</div>
                            <div class="metric-val">₹{total_rev:,.2f}</div>
                        </div>
                        <div class="metric-cell">
                            <div class="metric-label">Today's Orders</div>
                            <div class="metric-val" style="color: #8d2f00;">{today_orders}</div>
                        </div>
                    </div>

                    <div class="section-header">📊 Business Snapshot Highlights</div>
                    <table class="detail-table">
                        <tr>
                            <td><strong>Total Orders All-Time:</strong></td>
                            <td style="text-align: right; font-weight: 700;">{total_orders} orders</td>
                        </tr>
                        <tr>
                            <td><strong>Pending Deliveries:</strong></td>
                            <td style="text-align: right; font-weight: 700; color: #e67e22;">{pending_deliveries} active</td>
                        </tr>
                        <tr>
                            <td><strong>Low Stock Alerts (&lt;10kg):</strong></td>
                            <td style="text-align: right; font-weight: 700; color: #c0392b;">{low_stock_count} products</td>
                        </tr>
                    </table>

                    <div style="text-align: center; margin-top: 20px;">
                        <a href="{site_url}/admin-dashboard/" class="btn-cta">🚀 Open Admin Dashboard</a>
                    </div>
                </div>
                <div class="email-footer">
                    <strong>SMK Flour Shop Management System</strong><br>
                    Fresh Quality Batters & Traditional Products | Operations Automation
                </div>
            </div>
        </body>
        </html>
        """

        with IPv4OnlySMTP():
            sent = False
            err_msg = ""
            
            try:
                connection = get_connection(
                    host=smtp_host,
                    port=smtp_port,
                    username=smtp_user,
                    password=smtp_password,
                    use_tls=use_tls,
                    use_ssl=use_ssl,
                    timeout=20
                )
                email = EmailMultiAlternatives(
                    subject=f"🌾 SMK Flour Shop — Business Operations Report ({now_str})",
                    body=text_content,
                    from_email=smtp_user,
                    to=recipients,
                    connection=connection
                )
                email.attach_alternative(html_content, "text/html")
                email.attach('smk_flour_shop_report.pdf', pdf_data, 'application/pdf')
                email.send(fail_silently=False)
                sent = True
            except Exception as e1:
                err_msg = str(e1)
                # Auto Fallback for Gmail: If Port 587 timed out, try Port 465 (SSL)
                if smtp_host == 'smtp.gmail.com' and smtp_port == 587:
                    try:
                        fallback_conn = get_connection(
                            host='smtp.gmail.com',
                            port=465,
                            username=smtp_user,
                            password=smtp_password,
                            use_tls=False,
                            use_ssl=True,
                            timeout=20
                        )
                        email = EmailMultiAlternatives(
                            subject=f"🌾 SMK Flour Shop — Business Operations Report ({now_str})",
                            body=text_content,
                            from_email=smtp_user,
                            to=recipients,
                            connection=fallback_conn
                        )
                        email.attach_alternative(html_content, "text/html")
                        email.attach('smk_flour_shop_report.pdf', pdf_data, 'application/pdf')
                        email.send(fail_silently=False)
                        sent = True
                    except Exception as e2:
                        err_msg = f"Port 587: {err_msg} | Port 465: {str(e2)}"
                elif smtp_host == 'smtp.gmail.com' and smtp_port == 465:
                    try:
                        fallback_conn = get_connection(
                            host='smtp.gmail.com',
                            port=587,
                            username=smtp_user,
                            password=smtp_password,
                            use_tls=True,
                            use_ssl=False,
                            timeout=20
                        )
                        email = EmailMultiAlternatives(
                            subject=f"🌾 SMK Flour Shop — Business Operations Report ({now_str})",
                            body=text_content,
                            from_email=smtp_user,
                            to=recipients,
                            connection=fallback_conn
                        )
                        email.attach_alternative(html_content, "text/html")
                        email.attach('smk_flour_shop_report.pdf', pdf_data, 'application/pdf')
                        email.send(fail_silently=False)
                        sent = True
                    except Exception as e2:
                        err_msg = f"Port 465: {err_msg} | Port 587: {str(e2)}"

            if sent:
                return HttpResponse(f'<div style="padding: 0.8rem; background-color: #def7ec; border: 1px solid #84e1bc; color: #03543f; border-radius: 8px; font-weight: 600; margin-bottom: 1rem; font-size: 0.85rem;"><i class="fa-solid fa-paper-plane"></i> Email Report Sent Successfully to {", ".join(recipients)}!</div>', status=200)
            else:
                return HttpResponse(f'<div style="padding: 0.8rem; background-color: #fde8e8; border: 1px solid #f8b4b4; color: #9b1c1c; border-radius: 8px; font-weight: 600; margin-bottom: 1rem; font-size: 0.85rem;"><i class="fa-solid fa-triangle-exclamation"></i> Error sending email: {err_msg}</div>', status=200)
    except Exception as e:
        return HttpResponse(f'<div style="padding: 0.8rem; background-color: #fde8e8; border: 1px solid #f8b4b4; color: #9b1c1c; border-radius: 8px; font-weight: 600; margin-bottom: 1rem; font-size: 0.85rem;"><i class="fa-solid fa-triangle-exclamation"></i> Error sending email: {str(e)}</div>', status=200)

@staff_member_required
@require_POST
def admin_update_delivery_settings(request):
    from shop.models import DeliverySettings
    from decimal import Decimal
    
    settings_obj = DeliverySettings.get_settings()
    
    cost_per_km = request.POST.get('cost_per_km')
    base_fee = request.POST.get('base_delivery_fee')
    enable_online = 'enable_online_payment' in request.POST
    
    if cost_per_km:
        try:
            settings_obj.cost_per_km = Decimal(cost_per_km)
        except Exception:
            pass
            
    if base_fee:
        try:
            settings_obj.base_delivery_fee = Decimal(base_fee)
        except Exception:
            pass
            
    settings_obj.enable_online_payment = enable_online
    settings_obj.save()
    
    if getattr(request, 'htmx', False) or request.headers.get('HX-Request'):
        response = HttpResponse('<div style="padding: 0.8rem; background-color: #def7ec; border: 1px solid #84e1bc; color: #03543f; border-radius: 8px; font-weight: 600; margin-top: 0.5rem; font-size: 0.85rem;"><i class="fa-solid fa-circle-check"></i> Delivery & Payment Settings Saved Successfully!</div>')
        response['HX-Refresh'] = 'true'
        return response
        
    return redirect('admin_dashboard')

@staff_member_required
@require_POST
def admin_update_whatsapp_config(request):
    from shop.models import WhatsAppConfig
    config_obj = WhatsAppConfig.get_config()
    
    number = request.POST.get('whatsapp_number', '').strip()
    template = request.POST.get('otp_message_template', '').strip()
    
    if number:
        import re
        config_obj.whatsapp_number = re.sub(r'\D', '', number)[-10:]
    if template:
        config_obj.otp_message_template = template
        
    config_obj.save()
    
    if getattr(request, 'htmx', False) or request.headers.get('HX-Request'):
        response = HttpResponse('<div style="padding: 0.8rem; background-color: #def7ec; border: 1px solid #84e1bc; color: #03543f; border-radius: 8px; font-weight: 600; margin-top: 0.5rem; font-size: 0.85rem;"><i class="fa-solid fa-circle-check"></i> WhatsApp Gateway Settings Saved Successfully!</div>')
        response['HX-Refresh'] = 'true'
        return response
        
    return redirect('admin_dashboard')
