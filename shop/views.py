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
    seven_days_ago = today - timezone.timedelta(days=6)
    sales_trend_qs = Payment.objects.filter(
        created_at__date__gte=seven_days_ago,
        status='completed'
    ).annotate(date=TruncDate('created_at')).values('date').annotate(
        total=Sum('amount')
    ).order_by('date')
    
    # Pre-fill all 7 days with 0.0 to ensure continuous line chart
    sales_trend_dict = { (today - timezone.timedelta(days=i)): 0.0 for i in range(7) }
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
        try:
            product.bulk_stock = Decimal(request.POST.get('value', '0'))
            product.save(update_fields=['bulk_stock'])
        except Exception:
            pass
    elif target_type == 'packaged':
        slab_id = request.POST.get('slab_id')
        slab = get_object_or_404(PriceSlab, id=slab_id)
        try:
            slab.stock = int(request.POST.get('value', '0'))
            slab.save(update_fields=['stock'])
        except Exception:
            pass
    elif target_type == 'product_config':
        product_id = request.POST.get('product_id')
        product = get_object_or_404(Product, id=product_id)
        inventory_type = request.POST.get('inventory_type')
        bulk_unit = request.POST.get('bulk_unit')
        if inventory_type in ['bulk', 'packaged']:
            product.inventory_type = inventory_type
        if bulk_unit in ['kg', 'g', 'l', 'ml', 'piece']:
            product.bulk_unit = bulk_unit
        product.save()
        
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
