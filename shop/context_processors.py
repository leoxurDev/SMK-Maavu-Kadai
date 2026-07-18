from .models import PriceSlab, Customer

def customer_processor(request):
    customer_id = request.session.get('customer_id')
    current_customer = None
    if customer_id:
        try:
            current_customer = Customer.objects.get(id=customer_id)
        except Customer.DoesNotExist:
            pass
    return {
        'current_customer': current_customer,
    }

def cart_processor(request):
    cart_session = request.session.get('cart', {})
    cart_items = []
    cart_total = 0.0
    cart_count = 0

    if cart_session:
        # Load price slabs for items in cart
        slab_ids = []
        for sid in cart_session.keys():
            try:
                slab_ids.append(int(sid))
            except ValueError:
                pass

        slabs = PriceSlab.objects.filter(id__in=slab_ids).select_related('product')
        slabs_dict = {slab.id: slab for slab in slabs}

        for slab_id_str, quantity in cart_session.items():
            try:
                slab_id = int(slab_id_str)
            except ValueError:
                continue

            if slab_id in slabs_dict:
                slab = slabs_dict[slab_id]
                qty = int(quantity)
                if qty <= 0:
                    continue
                subtotal = float(slab.price) * qty
                cart_total += subtotal
                cart_count += qty
                cart_items.append({
                    'slab': slab,
                    'product': slab.product,
                    'quantity': qty,
                    'subtotal': subtotal
                })

    return {
        'cart_items': cart_items,
        'cart_total': cart_total,
        'cart_count': cart_count,
    }
