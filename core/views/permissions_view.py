"""Views quản lý phân quyền Role / Permission / UserRole."""

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models.permission import Permission, Role, RolePermission, UserRole

User = get_user_model()

PERMISSION_GROUPS = [
    ("Camera", [
        ("camera.view",     "Xem danh sách camera"),
        ("camera.add",      "Thêm camera"),
        ("camera.manage",   "Sửa cấu hình camera"),
        ("camera.delete",   "Xoá camera"),
        ("camera.assign",   "Gán quyền camera"),
    ]),
    ("Media", [
        ("media.view",      "Xem ảnh"),
        ("media.download",  "Tải ảnh"),
        ("media.delete",    "Xoá ảnh"),
    ]),
    ("User", [
        ("user.manage",     "Quản lý user"),
    ]),
    ("Client / Project", [
        ("client.view",     "Xem client / project"),
        ("client.manage",   "Quản lý client / project"),
    ]),
    ("Report / Render", [
        ("render.create",   "Tạo render video"),
    ]),
]

ALL_PERM_CODES = [code for _, perms in PERMISSION_GROUPS for code, _ in perms]


def _ensure_permissions():
    """Đảm bảo tất cả Permission objects đã tồn tại trong DB."""
    for code in ALL_PERM_CODES:
        Permission.objects.get_or_create(code=code, defaults={"description": code})


@login_required(login_url="/login/")
def permissions_overview(request):
    if not request.user.is_staff:
        return HttpResponseForbidden()

    _ensure_permissions()
    roles = Role.objects.prefetch_related("role_permissions__permission").order_by("name")

    role_data = []
    for role in roles:
        granted = {rp.permission.code for rp in role.role_permissions.all()}
        user_count = UserRole.objects.filter(role=role).count()
        role_data.append({
            "role": role,
            "granted": granted,
            "user_count": user_count,
        })

    all_perms = Permission.objects.order_by("code")

    return render(request, "home/permissions.html", {
        "segment": "permissions",
        "role_data": role_data,
        "permission_groups": PERMISSION_GROUPS,
        "all_perms": all_perms,
    })


@login_required(login_url="/login/")
def role_add(request):
    if not request.user.is_staff:
        return HttpResponseForbidden()

    _ensure_permissions()

    if request.method == "POST":
        code = request.POST.get("code", "").strip().lower().replace(" ", "_")
        name = request.POST.get("name", "").strip()
        if not code or not name:
            return render(request, "home/role_form.html", {
                "segment": "permissions",
                "error": "Code và tên không được để trống.",
                "form_data": request.POST,
                "permission_groups": PERMISSION_GROUPS,
                "action": "Thêm",
            })
        if Role.objects.filter(code=code).exists():
            return render(request, "home/role_form.html", {
                "segment": "permissions",
                "error": f"Role code '{code}' đã tồn tại.",
                "form_data": request.POST,
                "permission_groups": PERMISSION_GROUPS,
                "action": "Thêm",
            })
        role = Role.objects.create(code=code, name=name)
        selected = request.POST.getlist("perms")
        for perm_code in selected:
            perm, _ = Permission.objects.get_or_create(code=perm_code)
            RolePermission.objects.get_or_create(role=role, permission=perm)
        return redirect("permissions_overview")

    return render(request, "home/role_form.html", {
        "segment": "permissions",
        "permission_groups": PERMISSION_GROUPS,
        "action": "Thêm",
    })


@login_required(login_url="/login/")
def role_edit(request, pk):
    role = get_object_or_404(Role, pk=pk)
    if not request.user.is_staff:
        return HttpResponseForbidden()

    _ensure_permissions()

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            return render(request, "home/role_form.html", {
                "segment": "permissions",
                "role": role,
                "error": "Tên không được để trống.",
                "form_data": request.POST,
                "permission_groups": PERMISSION_GROUPS,
                "action": "Sửa",
            })
        role.name = name
        role.save()

        selected = set(request.POST.getlist("perms"))
        # Xoá permission không còn chọn
        RolePermission.objects.filter(role=role).exclude(
            permission__code__in=selected
        ).delete()
        # Thêm permission mới
        for perm_code in selected:
            if perm_code in ALL_PERM_CODES:
                perm, _ = Permission.objects.get_or_create(code=perm_code)
                RolePermission.objects.get_or_create(role=role, permission=perm)

        return redirect("permissions_overview")

    granted = {rp.permission.code for rp in role.role_permissions.all()}
    return render(request, "home/role_form.html", {
        "segment": "permissions",
        "role": role,
        "granted": granted,
        "permission_groups": PERMISSION_GROUPS,
        "action": "Sửa",
    })


@login_required(login_url="/login/")
def role_delete(request, pk):
    role = get_object_or_404(Role, pk=pk)
    if not request.user.is_staff:
        return HttpResponseForbidden()
    if request.method == "POST":
        role.delete()
    return redirect("permissions_overview")


@login_required(login_url="/login/")
def user_role_list(request):
    """Danh sách user + role đang gán."""
    if not request.user.is_staff:
        return HttpResponseForbidden()

    users = User.objects.prefetch_related(
        "user_roles__role"
    ).order_by("username")

    all_roles = Role.objects.order_by("name")
    user_data = []
    for u in users:
        roles = [ur.role for ur in u.user_roles.all()]
        user_data.append({"user": u, "roles": roles})

    return render(request, "home/user_roles.html", {
        "segment": "permissions",
        "user_data": user_data,
        "all_roles": all_roles,
    })


@login_required(login_url="/login/")
@require_POST
def user_role_assign(request, user_pk):
    """Gán/thu hồi role cho user."""
    if not request.user.is_staff:
        return HttpResponseForbidden()

    target = get_object_or_404(User, pk=user_pk)
    selected = set(request.POST.getlist("roles"))

    # Xoá role không còn chọn
    UserRole.objects.filter(user=target).exclude(role__id__in=selected).delete()
    # Thêm role mới
    for role_id in selected:
        try:
            role = Role.objects.get(pk=role_id)
            UserRole.objects.get_or_create(user=target, role=role)
        except Role.DoesNotExist:
            pass

    return redirect("user_role_list")
