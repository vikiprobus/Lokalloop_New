from django.contrib import admin
from .models import ShopCategory, Shop, ShopImage

@admin.register(ShopCategory)
class ShopCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'parent', 'is_active', 'sort_order')
    prepopulated_fields = {'slug': ('name',)}
    list_filter = ('is_active',)
    search_fields = ('name',)

class ShopImageInline(admin.TabularInline):
    model = ShopImage
    extra = 1

@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ('name', 'seller', 'category', 'locality', 'city', 'is_approved', 'is_active', 'created_at')
    list_filter = ('city', 'is_approved', 'is_active', 'category')
    search_fields = ('name', 'locality', 'address', 'seller__username')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ShopImageInline]
