from django.db import models
from django.conf import settings
import qrcode
from io import BytesIO
from django.core.files import File

class Shop(models.Model):
    name = models.CharField(max_length=255)
    address = models.TextField()
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    contact_number = models.CharField(max_length=20)
    qr_code = models.ImageField(upload_to='qr_codes/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        # First save to obtain ID if new
        super().save(*args, **kwargs)
        
        # Auto-generate QR code image if empty
        if not self.qr_code:
            site_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')
            qr_url = f"{site_url}/?shop_id={self.id}"
            
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(qr_url)
            qr.make(fit=True)

            img = qr.make_image(fill_color="black", back_color="white")
            
            buffer = BytesIO()
            img.save(buffer, format='PNG')
            
            self.qr_code.save(f"shop_qr_{self.id}.png", File(buffer), save=False)
            # Re-save with updated qr_code field
            super().save(update_fields=['qr_code'])

class Category(models.Model):
    name_en = models.CharField("Name (English)", max_length=100)
    name_ta = models.CharField("Name (Tamil)", max_length=100)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ['display_order', 'name_en']

    def __str__(self):
        return f"{self.name_en} / {self.name_ta}"

class Product(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='products')
    name_en = models.CharField("Name (English)", max_length=255)
    name_ta = models.CharField("Name (Tamil)", max_length=255)
    description_en = models.TextField("Description (English)", blank=True)
    description_ta = models.TextField("Description (Tamil)", blank=True)
    image = models.ImageField(upload_to='products/', null=True, blank=True)
    is_active = models.BooleanField(default=True)
    INVENTORY_CHOICES = [
        ('packaged', 'Packaged Units (by Packets/Slabs)'),
        ('bulk', 'Bulk Stock (by Total Weight/Volume)'),
    ]
    inventory_type = models.CharField(
        "Inventory Type",
        max_length=20,
        choices=INVENTORY_CHOICES,
        default='packaged'
    )
    bulk_stock = models.DecimalField(
        "Bulk Stock level",
        max_digits=10,
        decimal_places=2,
        default=100.00
    )
    bulk_unit = models.CharField(
        "Bulk Stock Unit",
        max_length=10,
        choices=[
            ('ml', 'ml'),
            ('l', 'Litre'),
            ('kg', 'kg'),
            ('g', 'g'),
            ('piece', 'Piece'),
        ],
        default='kg'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name_en']

    def __str__(self):
        return f"{self.name_en} / {self.name_ta}"

    @property
    def is_in_stock(self):
        if self.inventory_type == 'bulk':
            return self.bulk_stock > 0
        else:
            return any(slab.stock > 0 for slab in self.price_slabs.all())


class PriceSlab(models.Model):
    UNIT_CHOICES = [
        ('ml', 'ml'),
        ('l', 'Litre'),
        ('kg', 'kg'),
        ('g', 'g'),
        ('piece', 'Piece'),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='price_slabs')
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity_value = models.DecimalField(max_digits=10, decimal_places=2)
    quantity_unit = models.CharField(max_length=10, choices=UNIT_CHOICES)
    display_order = models.PositiveIntegerField(default=0)
    stock = models.PositiveIntegerField("Stock Inventory", default=100)

    class Meta:
        ordering = ['display_order', 'price']

    def __str__(self):
        # Format like: ₹30 - 600ml
        return f"₹{self.price} - {self.quantity_value} {self.get_quantity_unit_display()}"

    def is_in_stock(self, quantity=1):
        if self.product.inventory_type == 'bulk':
            needed = normalize_to_base(self.quantity_value, self.quantity_unit) * quantity
            available = normalize_to_base(self.product.bulk_stock, self.product.bulk_unit)
            return available >= needed
        else:
            return self.stock >= quantity

    def deduct_stock(self, quantity=1):
        if self.product.inventory_type == 'bulk':
            needed = normalize_to_base(self.quantity_value, self.quantity_unit) * quantity
            available = normalize_to_base(self.product.bulk_stock, self.product.bulk_unit)
            new_available = max(0, available - needed)
            if self.product.bulk_unit in ['kg', 'l']:
                self.product.bulk_stock = new_available / 1000
            else:
                self.product.bulk_stock = new_available
            self.product.save(update_fields=['bulk_stock'])
        else:
            self.stock = max(0, self.stock - quantity)
            self.save(update_fields=['stock'])

    def restore_stock(self, quantity=1):
        if self.product.inventory_type == 'bulk':
            needed = normalize_to_base(self.quantity_value, self.quantity_unit) * quantity
            available = normalize_to_base(self.product.bulk_stock, self.product.bulk_unit)
            new_available = available + needed
            if self.product.bulk_unit in ['kg', 'l']:
                self.product.bulk_stock = new_available / 1000
            else:
                self.product.bulk_stock = new_available
            self.product.save(update_fields=['bulk_stock'])
        else:
            self.stock += quantity
            self.save(update_fields=['stock'])

    def get_max_available_quantity(self):
        if self.product.inventory_type == 'bulk':
            needed = normalize_to_base(self.quantity_value, self.quantity_unit)
            if needed <= 0:
                return 0
            available = normalize_to_base(self.product.bulk_stock, self.product.bulk_unit)
            import math
            return int(math.floor(available / needed))
        else:
            return self.stock

def normalize_to_base(value, unit):
    from decimal import Decimal
    if unit in ['kg', 'l']:
        return Decimal(value) * 1000
    return Decimal(value)

class Customer(models.Model):
    phone_number = models.CharField(max_length=15, unique=True)
    name = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or self.phone_number

class Address(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='addresses')
    address_text = models.TextField()
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    landmark = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Addresses"

    def __str__(self):
        return f"{self.landmark or 'Address'} - {self.address_text[:30]}..."

class Order(models.Model):
    ORDER_TYPE_CHOICES = [
        ('pickup', 'Store Pickup'),
        ('delivery', 'Home Delivery'),
    ]

    STATUS_CHOICES = [
        ('received', 'Received'),
        ('preparing', 'Preparing'),
        ('ready', 'Ready for Pickup / Out for Delivery'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='orders')
    order_type = models.CharField(max_length=10, choices=ORDER_TYPE_CHOICES)
    address = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='received')
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    assigned_delivery = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_deliveries',
        verbose_name="Assigned Delivery Staff"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def items_total(self):
        return self.total_amount - self.delivery_fee

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Order #{self.id} - {self.customer} ({self.get_status_display()})"

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    price_slab = models.ForeignKey(PriceSlab, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.quantity} x {self.product.name_en} ({self.price_slab})"

class Payment(models.Model):
    METHOD_CHOICES = [
        ('online', 'Online Payment (Razorpay)'),
        ('cod', 'Cash on Delivery (COD)'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='payment')
    method = models.CharField(max_length=10, choices=METHOD_CHOICES)
    gateway_txn_id = models.CharField("Gateway Transaction ID", max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment for Order #{self.order.id} - {self.get_status_display()}"
