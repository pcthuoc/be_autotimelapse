from django.core.management.base import BaseCommand

from core.models import Permission, Role, RolePermission

# ------------------------------------------------------------------ #
# Dữ liệu seed mặc định
# ------------------------------------------------------------------ #

PERMISSIONS = [
    ("camera.view",     "View camera list and details"),
    ("camera.add",      "Add new camera"),
    ("camera.manage",   "Edit camera config / name / status"),
    ("camera.delete",   "Delete camera"),
    ("camera.assign",   "Assign camera to user"),
    ("media.view",      "View media / photo list"),
    ("media.download",  "Download photos"),
    ("media.delete",    "Delete photos"),
    ("user.manage",     "Create / disable / edit user accounts"),
    ("audit.view",      "View audit log"),
]

ROLES = [
    ("admin",    "Administrator"),
    ("operator", "Operator"),
    ("viewer",   "Viewer"),
]

# role_code → [permission_code, ...]
ROLE_PERMISSIONS = {
    "admin": [p[0] for p in PERMISSIONS],   # tất cả
    "operator": [
        "camera.view", "camera.manage",
        "media.view", "media.download", "media.delete",
    ],
    "viewer": [
        "camera.view",
        "media.view",
    ],
}


class Command(BaseCommand):
    help = "Seed default Roles, Permissions and RolePermissions."

    def handle(self, *args, **kwargs):
        # 1. Tạo permissions
        perm_map = {}
        for code, desc in PERMISSIONS:
            obj, created = Permission.objects.get_or_create(
                code=code, defaults={"description": desc}
            )
            perm_map[code] = obj
            self.stdout.write(
                f"  {'[+]' if created else '[ ]'} Permission: {code}"
            )

        # 2. Tạo roles
        role_map = {}
        for code, name in ROLES:
            obj, created = Role.objects.get_or_create(
                code=code, defaults={"name": name}
            )
            role_map[code] = obj
            self.stdout.write(
                f"  {'[+]' if created else '[ ]'} Role: {code}"
            )

        # 3. Gán permission vào role
        for role_code, perm_codes in ROLE_PERMISSIONS.items():
            role = role_map[role_code]
            for perm_code in perm_codes:
                _, created = RolePermission.objects.get_or_create(
                    role=role, permission=perm_map[perm_code]
                )
                if created:
                    self.stdout.write(
                        f"  [+] {role_code} → {perm_code}"
                    )

        self.stdout.write(self.style.SUCCESS("\nSeed complete!"))
