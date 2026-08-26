from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie

from ._helpers import get_user_membership, _user_perms

User = get_user_model()


@api_view(['GET'])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def api_csrf(request):
    return Response({'csrfToken': get_token(request)})


@api_view(['POST'])
@permission_classes([AllowAny])
@csrf_protect
def api_login(request):
    user = authenticate(request, username=request.data.get('username', ''),
                        password=request.data.get('password', ''))
    if not user:
        return Response({'detail': 'Sai username hoặc password'}, status=status.HTTP_401_UNAUTHORIZED)
    auth_login(request, user)
    if request.data.get('remember', False):
        request.session.set_expiry(60 * 60 * 24 * 14)
    else:
        request.session.set_expiry(0)
    return Response({'id': user.id, 'username': user.username, 'is_staff': user.is_staff})


@api_view(['POST'])
def api_logout(request):
    auth_logout(request)
    return Response({'ok': True})


@api_view(['GET'])
def api_me(request):
    u = request.user
    if not u or not u.is_authenticated:
        return Response({'detail': 'Chưa đăng nhập'}, status=status.HTTP_401_UNAUTHORIZED)
    m = get_user_membership(u)
    client_role = 'superadmin' if u.is_staff else (m.role if m else None)
    return Response({
        'id': u.id, 'username': u.username, 'email': u.email,
        'is_staff': u.is_staff, 'is_active': u.is_active,
        'date_joined': u.date_joined.isoformat(),
        'client_id': str(m.client_id) if m else None,
        'client_name': m.client.name if m else None,
        'client_role': client_role,
        'perms': _user_perms(u),
    })
