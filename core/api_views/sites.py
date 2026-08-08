from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.models import Site, Client
from ._helpers import filter_sites_for_user, get_user_membership, is_client_admin


@api_view(['GET', 'POST'])
def api_sites(request):
    if request.method == 'GET':
        sites = filter_sites_for_user(request.user).order_by('name')
        return Response([{'id': str(s.id), 'name': s.name, 'description': s.description,
                          'location': getattr(s, 'location', ''),
                          'client_id': str(s.client_id) if s.client_id else None,
                          'cam_count': s.cameras.count()} for s in sites])
    if not (request.user.is_staff or is_client_admin(request.user)):
        return Response({'detail': 'Permission denied'}, status=403)
    name = request.data.get('name', '').strip()
    if not name:
        return Response({'detail': 'name required'}, status=400)
    site = Site.objects.create(name=name,
                               description=request.data.get('description', ''),
                               location=request.data.get('location', ''))
    if not request.user.is_staff:
        m = get_user_membership(request.user)
        if m:
            site.client = m.client
            site.save(update_fields=['client', 'updated_at'])
    return Response({'id': str(site.id), 'name': site.name}, status=201)


@api_view(['GET', 'PATCH', 'DELETE'])
def api_site_detail(request, pk):
    try:
        site = Site.objects.get(pk=pk)
    except Site.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    # GET: any member of the client can read; PATCH/DELETE: admin only
    if request.method == 'GET':
        m = get_user_membership(request.user)
        if not request.user.is_staff:
            if not m or site.client_id != m.client_id:
                return Response({'detail': 'Permission denied'}, status=403)
    else:
        if not (request.user.is_staff or is_client_admin(request.user)):
            return Response({'detail': 'Permission denied'}, status=403)

    if request.method == 'GET':
        return Response({'id': str(site.id), 'name': site.name, 'description': site.description,
                         'location': getattr(site, 'location', ''),
                         'client_id': str(site.client_id) if site.client_id else None,
                         'cam_count': site.cameras.count()})
    if request.method == 'PATCH':
        for f in ['name', 'description', 'location']:
            if f in request.data:
                setattr(site, f, str(request.data[f]).strip() if f == 'name' else str(request.data[f]))
        site.save()
        return Response({'ok': True, 'name': site.name})
    site.delete()
    return Response({'ok': True})


@api_view(['POST'])
def api_site_assign_client(request, pk):
    if not (request.user.is_staff or is_client_admin(request.user)):
        return Response({'detail': 'Permission denied'}, status=403)
    try:
        site = Site.objects.get(pk=pk)
    except Site.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    m = get_user_membership(request.user)
    if not request.user.is_staff:
        if not m or m.role != 'admin':
            return Response({'detail': 'Permission denied'}, status=403)
        if site.client_id not in (None, m.client_id):
            return Response({'detail': 'Permission denied'}, status=403)

    client_id = request.data.get('client_id')
    if client_id:
        if not request.user.is_staff and str(client_id) != str(m.client_id):
            return Response({'detail': 'Permission denied'}, status=403)
        try:
            site.client = Client.objects.get(pk=client_id)
        except Client.DoesNotExist:
            return Response({'detail': 'Client not found'}, status=404)
    else:
        site.client = None
    site.save(update_fields=['client', 'updated_at'])
    return Response({'ok': True})
