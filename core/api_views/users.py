from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.core.exceptions import ValidationError as _VE
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from ._helpers import get_user_membership, is_client_admin

User = get_user_model()


@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
def api_users(request):
    if request.method == 'GET':
        qs = User.objects.select_related('profile').all().order_by('username')
        if q := request.query_params.get('q', ''):
            qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q))
        from core.models.permission import UserRole
        roles_map = {}
        for ur in UserRole.objects.select_related('role').filter(user__in=qs):
            roles_map.setdefault(ur.user_id, []).append(
                {'id': str(ur.role.id), 'code': ur.role.code, 'name': ur.role.name})
        return Response({'results': [
            {'id': u.id, 'username': u.username, 'email': u.email,
             'full_name': getattr(getattr(u, 'profile', None), 'full_name', '') or '',
             'is_staff': u.is_staff, 'is_active': u.is_active,
             'roles': roles_map.get(u.id, []), 'date_joined': u.date_joined.isoformat()}
            for u in qs
        ], 'count': qs.count()})

    username = (request.data.get('username') or '').strip()
    password = request.data.get('password') or ''
    if not username or not password:
        return Response({'detail': 'username và password là bắt buộc'}, status=400)
    if User.objects.filter(username=username).exists():
        return Response({'detail': 'Username đã tồn tại'}, status=400)
    if len(password) < 8:
        return Response({'detail': 'Password tối thiểu 8 ký tự'}, status=400)
    u = User.objects.create_user(username=username, password=password,
                                 email=request.data.get('email', ''),
                                 is_staff=bool(request.data.get('is_staff', False)))
    if full_name := (request.data.get('full_name') or '').strip():
        if hasattr(u, 'profile'):
            u.profile.full_name = full_name
            u.profile.save(update_fields=['full_name', 'updated_at'])
    if role_id := request.data.get('role_id'):
        from core.models.permission import Role, UserRole
        try:
            UserRole.objects.get_or_create(user=u, role=Role.objects.get(pk=role_id))
        except Role.DoesNotExist:
            pass
    return Response({'id': u.id, 'username': u.username}, status=201)


@api_view(['PATCH'])
@permission_classes([IsAdminUser])
def api_user_detail(request, pk):
    try:
        u = User.objects.get(pk=pk)
    except User.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    if 'email' in request.data:
        email = (request.data.get('email') or '').strip()
        if email:
            try:
                validate_email(email)
            except _VE:
                return Response({'detail': 'Email không hợp lệ'}, status=400)
        u.email = email
    for f in ('is_staff', 'is_active'):
        if f in request.data:
            setattr(u, f, bool(request.data[f]))
    if password := request.data.get('password'):
        if len(password) < 8:
            return Response({'detail': 'Password tối thiểu 8 ký tự'}, status=400)
        u.set_password(password)
    u.save()
    if (full_name := request.data.get('full_name')) is not None and hasattr(u, 'profile'):
        u.profile.full_name = full_name.strip()
        u.profile.save(update_fields=['full_name', 'updated_at'])
    if 'role_id' in request.data:
        from core.models.permission import Role, UserRole
        UserRole.objects.filter(user=u).delete()
        if role_id := request.data.get('role_id'):
            try:
                UserRole.objects.get_or_create(user=u, role=Role.objects.get(pk=role_id))
            except Role.DoesNotExist:
                pass
    return Response({'id': u.id, 'username': u.username})


@api_view(['GET'])
def api_user_search(request):
    if not (request.user.is_staff or is_client_admin(request.user)):
        return Response({'results': []})
    q = (request.query_params.get('q') or '').strip()
    qs = User.objects.filter(is_active=True)
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q))
    return Response({'results': [
        {'id': u.id, 'username': u.username, 'email': u.email,
         'full_name': getattr(getattr(u, 'profile', None), 'full_name', '') or ''}
        for u in qs.order_by('username')[:10]
    ]})


@api_view(['GET'])
def api_roles(request):
    if not request.user.is_staff:
        return Response({'results': []})
    from core.models.permission import Role
    return Response({'results': [{'id': str(r.id), 'code': r.code, 'name': r.name}
                                  for r in Role.objects.all().order_by('name')]})


@api_view(['POST'])
@permission_classes([IsAdminUser])
def api_user_toggle(request, pk):
    try:
        u = User.objects.get(pk=pk)
    except User.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    u.is_active = not u.is_active
    u.save()
    return Response({'is_active': u.is_active})
