"""Views quản lý Client (Khách hàng) — hỗ trợ AJAX modal."""

import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models.camera import Camera, Client, Site
from core.models.media import Media
from core.models.permission import ClientMembership


def _has_perm(user, code):
    if user.is_staff:
        return True
    m = ClientMembership.objects.filter(user=user).first()
    if not m:
        return False
    if code in ('camera.view', 'media.view'):
        return True
    return m.role == 'admin'


def _is_ajax(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@login_required(login_url="/login/")
def client_list(request):
    if not _has_perm(request.user, "camera.view"):
        return HttpResponseForbidden()

    clients = Client.objects.prefetch_related("projects__cameras").order_by("name")
    client_data = []
    for c in clients:
        projects = c.projects.all()
        cam_count = sum(p.cameras.count() for p in projects)
        client_data.append({
            "client": c,
            "project_count": projects.count(),
            "cam_count": cam_count,
        })

    return render(request, "home/client_list.html", {
        "segment": "clients",
        "client_data": client_data,
        "can_manage": _has_perm(request.user, "camera.manage"),
    })


@login_required(login_url="/login/")
def client_detail(request, pk):
    client = get_object_or_404(Client, pk=pk)
    if not _has_perm(request.user, "camera.view"):
        return HttpResponseForbidden()

    projects = client.projects.prefetch_related("cameras").order_by("name")
    project_data = []
    for proj in projects:
        cams = proj.cameras.all()
        latest = (
            Media.objects.filter(camera__in=cams)
            .order_by("-taken_at")
            .first()
        )
        project_data.append({
            "project": proj,
            "cam_count": cams.count(),
            "latest_media": latest,
        })

    return render(request, "home/client_detail.html", {
        "segment": "clients",
        "client": client,
        "project_data": project_data,
        "can_manage": _has_perm(request.user, "camera.manage"),
    })


@login_required(login_url="/login/")
def client_save(request):
    """Add hoặc Edit Client — nhận POST từ modal, trả JSON."""
    if not _has_perm(request.user, "camera.manage"):
        return JsonResponse({"ok": False, "error": "Không có quyền."}, status=403)

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed."}, status=405)

    pk = request.POST.get("pk", "").strip()
    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Tên khách hàng không được để trống."}, status=400)

    if pk:
        client = get_object_or_404(Client, pk=pk)
    else:
        client = Client()

    client.name = name
    client.contact_name = request.POST.get("contact_name", "").strip()
    client.contact_email = request.POST.get("contact_email", "").strip()
    client.phone = request.POST.get("phone", "").strip()
    client.address = request.POST.get("address", "").strip()
    client.notes = request.POST.get("notes", "").strip()
    client.save()

    return JsonResponse({
        "ok": True,
        "pk": str(client.pk),
        "name": client.name,
        "contact_name": client.contact_name,
        "contact_email": client.contact_email,
        "phone": client.phone,
        "address": client.address,
        "notes": client.notes,
        "is_new": not bool(pk),
    })


@login_required(login_url="/login/")
@require_POST
def client_delete(request, pk):
    """Xoá Client — trả JSON."""
    client = get_object_or_404(Client, pk=pk)
    if not _has_perm(request.user, "camera.manage"):
        return JsonResponse({"ok": False, "error": "Không có quyền."}, status=403)
    name = client.name
    client.delete()
    return JsonResponse({"ok": True, "name": name})


@login_required(login_url="/login/")
def client_add(request):
    return redirect("client_list")


@login_required(login_url="/login/")
def client_edit(request, pk):
    return redirect("client_list")
