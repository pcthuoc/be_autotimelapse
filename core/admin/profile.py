from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from core.models import Profile


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = "Profiles"
    fields = ("full_name", "status")


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "full_name", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("user__username", "user__email", "full_name")
    readonly_fields = ("created_at", "updated_at")
    fields = ("user", "full_name", "status", "created_at", "updated_at")


# Gắn Profile inline vào trang User có sẵn
class UserAdmin(BaseUserAdmin):
    inlines = (ProfileInline,)


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
