from django.contrib import admin
from .models import Shop, Category, Product, PriceSlab, Customer, Address, Order, OrderItem, Payment

class PriceSlabInline(admin.TabularInline):
    model = PriceSlab
    extra = 1
    fields = ('price', 'quantity_value', 'quantity_unit', 'display_order')
    ordering = ('display_order', 'price')

from django.utils.html import format_html

@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_number', 'latitude', 'longitude', 'created_at')
    search_fields = ('name', 'contact_number', 'address')
    readonly_fields = ('created_at', 'updated_at', 'qr_code_preview')
    fields = ('name', 'address', 'contact_number', 'latitude', 'longitude', 'qr_code', 'qr_code_preview')

    def qr_code_preview(self, obj):
        if obj.qr_code:
            return format_html('<img src="{}" width="200" height="200" style="border: 1px solid #ccc; border-radius: 4px;" />', obj.qr_code.url)
        return "Will be generated automatically upon saving."
    qr_code_preview.short_description = 'QR Code Preview'

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name_en', 'name_ta', 'display_order')
    list_editable = ('display_order',)
    search_fields = ('name_en', 'name_ta')
    ordering = ('display_order', 'name_en')

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name_en', 'name_ta', 'category', 'is_active', 'price_slabs_summary')
    list_filter = ('category', 'is_active')
    search_fields = ('name_en', 'name_ta', 'description_en', 'description_ta')
    inlines = [PriceSlabInline]
    ordering = ('category', 'name_en')

    def price_slabs_summary(self, obj):
        slabs = obj.price_slabs.all()
        return ", ".join([f"₹{s.price} ({s.quantity_value} {s.get_quantity_unit_display()})" for s in slabs])
    price_slabs_summary.short_description = 'Pricing Slabs'

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('product', 'price_slab', 'quantity', 'subtotal')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer', 'order_type', 'status', 'total_amount', 'created_at')
    list_filter = ('status', 'order_type', 'created_at')
    search_fields = ('id', 'customer__phone_number', 'customer__name')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [OrderItemInline]
    ordering = ('-created_at',)

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('phone_number', 'name', 'created_at')
    search_fields = ('phone_number', 'name')
    ordering = ('-created_at',)

@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ('customer', 'landmark', 'address_text', 'latitude', 'longitude')
    search_fields = ('customer__phone_number', 'customer__name', 'address_text', 'landmark')

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('order', 'method', 'status', 'amount', 'gateway_txn_id', 'paid_at')
    list_filter = ('method', 'status', 'paid_at')
    search_fields = ('order__id', 'gateway_txn_id', 'order__customer__phone_number')
    readonly_fields = ('created_at',)
    ordering = ('-created_at',)
