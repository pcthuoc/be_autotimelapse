from django.contrib import admin

from core.models import ClientMembership, Permission, Role, RolePermission, UserRole


class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 1
    fields = ("permission",)
    autocomplete_fields = ("permission",)


class UserRoleInline(admin.TabularInline):
    model = UserRole
    extra = 0
    fields = ("user",)
    autocomplete_fields = ("user",)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "permission_count", "user_count")
    search_fields = ("code", "name")
    inlines = (RolePermissionInline, UserRoleInline)

    @admin.display(description="Permissions")
    def permission_count(self, obj):
        return obj.role_permissions.count()

    @admin.display(description="Users")
    def user_count(self, obj):
        return obj.user_roles.count()


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("code", "description")
    search_fields = ("code", "description")


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role")
    list_filter = ("role",)
    search_fields = ("user__username",)
    autocomplete_fields = ("user", "role")


@admin.register(ClientMembership)
class ClientMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "client", "role", "can_download", "joined_at")
    list_filter = ("role", "client")
    search_fields = ("user__username", "client__name")
    autocomplete_fields = ("user", "invited_by")
