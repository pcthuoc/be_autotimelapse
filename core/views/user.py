from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from core.forms import UserCreateForm, UserEditForm
from core.models.permission import Role, UserRole
from core.views.camera import _has_perm

User = get_user_model()
_PAGE_SIZE = 20


# ── helpers ───────────────────────────────────────────────────────────────────

def _user_qs(search=""):
    qs = (
        User.objects
        .prefetch_related("user_roles__role")
        .order_by("username")
    )
    if search:
        qs = qs.filter(username__icontains=search) | qs.filter(email__icontains=search)
    return qs.distinct()


def _require_user_manage(user):
    if not _has_perm(user, "user.manage"):
        raise Http404()


# ── views ─────────────────────────────────────────────────────────────────────

@login_required
def user_list(request):
    _require_user_manage(request.user)

    search = request.GET.get("q", "").strip()
    qs = _user_qs(search)
    paginator = Paginator(qs, _PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Pre-build edit forms for all users on this page
    rows = [(u, UserEditForm(instance=u)) for u in page_obj]

    return render(request, "home/users.html", {
        "segment": "users",
        "page_obj": page_obj,
        "rows": rows,
        "search": search,
        "add_form": UserCreateForm(),
        "roles": Role.objects.order_by("name"),
    })


@login_required
def user_add(request):
    _require_user_manage(request.user)
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, f"User {user.username} created.")
            return redirect("user_list")
        search = request.GET.get("q", "").strip()
        qs = _user_qs(search)
        paginator = Paginator(qs, _PAGE_SIZE)
        page_obj = paginator.get_page(1)
        return render(request, "home/users.html", {
            "segment": "users",
            "page_obj": page_obj,
            "rows": [(u, UserEditForm(instance=u)) for u in page_obj],
            "search": search,
            "add_form": form,
            "roles": Role.objects.order_by("name"),
            "open_add_modal": True,
        })
    return redirect("user_list")


@login_required
def user_edit(request, pk):
    _require_user_manage(request.user)
    target = get_object_or_404(User, pk=pk)
    # Không cho sửa chính mình qua trang này
    if target == request.user and not request.user.is_superuser:
        messages.error(request, "You cannot edit your own account here.")
        return redirect("user_list")
    if request.method == "POST":
        form = UserEditForm(request.POST, instance=target)
        if form.is_valid():
            form.save()
            messages.success(request, f"User {target.username} updated.")
            return redirect("user_list")
        search = request.GET.get("q", "").strip()
        qs = _user_qs(search)
        paginator = Paginator(qs, _PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get("page", 1))
        rows = []
        for u in page_obj:
            ef = form if u.pk == target.pk else UserEditForm(instance=u)
            rows.append((u, ef))
        return render(request, "home/users.html", {
            "segment": "users",
            "page_obj": page_obj,
            "rows": rows,
            "search": search,
            "add_form": UserCreateForm(),
            "roles": Role.objects.order_by("name"),
            "open_edit_pk": str(target.pk),
        })
    return redirect("user_list")


@login_required
def user_toggle(request, pk):
    """Kích hoạt / vô hiệu hoá tài khoản."""
    _require_user_manage(request.user)
    target = get_object_or_404(User, pk=pk)
    if target == request.user:
        messages.error(request, "You cannot disable your own account.")
        return redirect("user_list")
    if request.method == "POST":
        target.is_active = not target.is_active
        target.save(update_fields=["is_active"])
        status = "activated" if target.is_active else "disabled"
        messages.success(request, f"Account {target.username} {status}.")
    return redirect("user_list")
