from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.core.exceptions import ValidationError as _VE
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.models import Client, Site
from ._helpers import get_user_membership, is_client_admin

User = get_user_model()


def _can_manage_client(user, client):
    if user.is_staff:
        return True
    m = get_user_membership(user)
    return bool(m and m.role == 'admin' and m.client_id == client.id)


def _membership_to_dict(m):
    return {
        'user_id': m.user_id, 'username': m.user.username, 'email': m.user.email or '',
        'full_name': getattr(getattr(m.user, 'profile', None), 'full_name', '') or '',
        'role': m.role, 'can_download': m.can_download,
        'joined_at': m.joined_at.isoformat(),
    }


def _client_to_dict(c):
    projects = []
    for p in c.projects.all():
        cams = [{'id': str(cm.id), 'code': cm.code, 'name': cm.name, 'status': cm.status}
                for cm in p.cameras.all()]
        projects.append({'id': str(p.id), 'name': p.name, 'location': p.location,
                         'start_date': str(p.start_date) if p.start_date else None,
                         'end_date': str(p.end_date) if p.end_date else None,
                         'cameras': cams, 'cam_count': len(cams)})
    return {
        'id': str(c.id), 'name': c.name,
        'contact_name': c.contact_name, 'contact_email': c.contact_email,
        'phone': c.phone, 'address': c.address, 'notes': c.notes,
        'projects': projects,
        'project_count': len(projects),
        'camera_count': sum(p['cam_count'] for p in projects),
        'created_at': c.created_at.isoformat(),
    }


@api_view(['GET', 'POST'])
def api_clients(request):
    m = get_user_membership(request.user)
    if request.method == 'GET':
        if request.user.is_staff:
            clients = Client.objects.prefetch_related('projects__cameras').all()
        elif m and m.role == 'admin':
            clients = Client.objects.prefetch_related('projects__cameras').filter(pk=m.client_id)
        else:
            return Response({'results': [], 'count': 0})
        data = [_client_to_dict(c) for c in clients]
        return Response({'results': data, 'count': len(data)})

    if not request.user.is_staff:
        return Response({'detail': 'Permission denied'}, status=403)
    name = (request.data.get('name') or '').strip()
    if not name:
        return Response({'detail': 'name required'}, status=400)
    c = Client.objects.create(
        name=name,
        contact_name=request.data.get('contact_name', ''),
        contact_email=request.data.get('contact_email', ''),
        phone=request.data.get('phone', ''),
        address=request.data.get('address', ''),
        notes=request.data.get('notes', ''),
    )
    return Response({'id': str(c.id), 'name': c.name}, status=201)


@api_view(['GET', 'PATCH', 'DELETE'])
def api_client_detail(request, pk):
    try:
        c = Client.objects.prefetch_related('projects__cameras').get(pk=pk)
    except Client.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if not request.user.is_staff:
        m = get_user_membership(request.user)
        if not m or m.client_id != c.id:
            return Response({'detail': 'Permission denied'}, status=403)

    if request.method == 'DELETE':
        if not request.user.is_staff:
            return Response({'detail': 'Permission denied'}, status=403)
        c.delete()
        return Response({'ok': True})

    if request.method == 'PATCH':
        if not _can_manage_client(request.user, c):
            return Response({'detail': 'Permission denied'}, status=403)
        if 'contact_email' in request.data and request.data['contact_email']:
            try:
                validate_email(str(request.data['contact_email']))
            except _VE:
                return Response({'detail': 'contact_email không hợp lệ'}, status=400)
        for f in ('name', 'contact_name', 'contact_email', 'phone', 'address', 'notes'):
            if f in request.data:
                setattr(c, f, request.data[f])
        c.save()

    return Response(_client_to_dict(c))


@api_view(['GET', 'POST'])
def api_client_members(request, pk):
    from core.models import ClientMembership
    try:
        client = Client.objects.get(pk=pk)
    except Client.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    if not _can_manage_client(request.user, client):
        return Response({'detail': 'Permission denied'}, status=403)

    if request.method == 'GET':
        members = (ClientMembership.objects.select_related('user', 'user__profile')
                   .filter(client=client).order_by('-role', 'user__username'))
        return Response({'results': [_membership_to_dict(m) for m in members]})

    user_id  = request.data.get('user_id')
    username = (request.data.get('username') or '').strip()
    role     = request.data.get('role', 'member')
    if role not in ('admin', 'member'):
        return Response({'detail': 'role phải là admin hoặc member'}, status=400)
    if role == 'admin' and not request.user.is_staff:
        return Response({'detail': 'Chỉ superadmin mới cấp quyền admin'}, status=403)

    target = User.objects.filter(pk=user_id).first() if user_id else \
             User.objects.filter(username=username).first() if username else None
    if not target:
        return Response({'detail': 'Không tìm thấy user'}, status=404)
    if target.is_staff:
        return Response({'detail': 'Không thể gán superadmin vào client'}, status=400)

    existing = ClientMembership.objects.filter(user=target).first()
    if existing and existing.client_id != client.id:
        return Response({'detail': f'User đã thuộc client khác ({existing.client.name})'}, status=400)

    m, created = ClientMembership.objects.update_or_create(
        user=target, client=client,
        defaults={'role': role, 'can_download': bool(request.data.get('can_download', True)),
                  'invited_by': request.user},
    )
    return Response(_membership_to_dict(m), status=201 if created else 200)


@api_view(['PATCH', 'DELETE'])
def api_client_member_detail(request, pk, user_id):
    from core.models import ClientMembership
    try:
        client = Client.objects.get(pk=pk)
    except Client.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    if not _can_manage_client(request.user, client):
        return Response({'detail': 'Permission denied'}, status=403)

    m = ClientMembership.objects.select_related('user', 'user__profile').filter(
        client=client, user_id=user_id).first()
    if not m:
        return Response({'detail': 'Member không tồn tại'}, status=404)

    if request.method == 'DELETE':
        m.delete()
        return Response({'ok': True})

    if 'role' in request.data:
        new_role = request.data['role']
        if new_role not in ('admin', 'member'):
            return Response({'detail': 'role không hợp lệ'}, status=400)
        if new_role == 'admin' and not request.user.is_staff:
            return Response({'detail': 'Chỉ superadmin mới cấp quyền admin'}, status=403)
        m.role = new_role
    if 'can_download' in request.data:
        m.can_download = bool(request.data['can_download'])
    m.save()
    return Response(_membership_to_dict(m))
