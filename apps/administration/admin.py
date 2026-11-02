from django.contrib import admin
from .models import AdminAction, SupportTicket, MerchantAgreementLog, ErrorLog, PlatformSetting, ContactInquiry

@admin.register(ContactInquiry)
class ContactInquiryAdmin(admin.ModelAdmin):
    list_display = ('ticket_number', 'full_name', 'email', 'user_role', 'status', 'created_at')
    list_filter = ('user_role', 'status', 'created_at')
    search_fields = ('ticket_number', 'full_name', 'email', 'subject', 'message')
    readonly_fields = ('ticket_number', 'ip_address', 'user_agent', 'created_at')


@admin.register(AdminAction)
class AdminActionAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'admin', 'action_type', 'target_model', 'target_id')
    list_filter = ('action_type', 'target_model', 'created_at')
    search_fields = ('action_type', 'details', 'target_id')

@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'subject', 'category', 'status', 'created_at')
    list_filter = ('category', 'status', 'created_at')
    search_fields = ('subject', 'message', 'user__username')

@admin.register(MerchantAgreementLog)
class MerchantAgreementLogAdmin(admin.ModelAdmin):
    list_display = ('accepted_at', 'merchant_id', 'business_name', 'user', 'ip_address', 'is_accepted')
    list_filter = ('is_accepted', 'accepted_at')
    search_fields = ('merchant_id', 'business_name', 'user__username', 'ip_address')
    readonly_fields = ('user', 'merchant_id', 'business_name', 'agreement_title', 'agreement_url', 'agreement_version', 'ip_address', 'user_agent', 'is_accepted', 'accepted_at')

