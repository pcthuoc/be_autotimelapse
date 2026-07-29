"""
context_processors.py

Inject ``user_perms`` dict vào mọi template context.
Một DB query duy nhất cho non-staff user mỗi request.
"""


def user_perms(request):
    """
    Template context:
        {{ user_perms.camera_view }}  → True/False
        {{ user_perms.user_manage }}  → True/False
        ...
    """
    if not request.user.is_authenticated:
        return {"user_perms": {}}

    u = request.user
    if u.is_staff:
        return {
            "user_perms": {
                "camera_view":    True,
                "camera_add":     True,
                "camera_manage":  True,
                "camera_delete":  True,
                "camera_assign":  True,
                "media_view":     True,
                "media_download": True,
                "media_delete":   True,
                "user_manage":    True,
                "client_view":    True,
                "client_manage":  True,
                "render_create":  True,
            }
        }

    # Một query: lấy hết permission codes của user qua mọi role
    from core.models.permission import UserRole
    codes = set(
        UserRole.objects
        .filter(user=u)
        .values_list("role__role_permissions__permission__code", flat=True)
    )

    return {
        "user_perms": {
            "camera_view":    "camera.view"    in codes,
            "camera_add":     "camera.add"     in codes,
            "camera_manage":  "camera.manage"  in codes,
            "camera_delete":  "camera.delete"  in codes,
            "camera_assign":  "camera.assign"  in codes,
            "media_view":     "media.view"     in codes,
            "media_download": "media.download" in codes,
            "media_delete":   "media.delete"   in codes,
            "user_manage":    "user.manage"    in codes,
            "client_view":    "client.view"    in codes,
            "client_manage":  "client.manage"  in codes,
            "render_create":  "render.create"  in codes,
        }
    }
