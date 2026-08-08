"""
URL configuration for autotimelapse project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf.urls.i18n import i18n_patterns
from django.contrib.auth.views import LogoutView
from django.urls import path, re_path, include

from core.views.auth import login_view, register_user
from core.views.camera import (
    camera_add, camera_delete, camera_detail, camera_edit, camera_list, site_add,
    camera_live, camera_live_latest, camera_live_settings,
    camera_device_modal, camera_device_settings, camera_device_wake,
    camera_device_sim, camera_settings_save, camera_settings_pull,
    camera_device_state,
    live_view_start, live_view_stop, live_view_frame,
)
from core.views.client import client_list, client_detail, client_add, client_edit, client_delete, client_save
from core.views import device_api
from core.views.dashboard import dashboard
from core.views.home import index, pages
from core.views.media import (
    media_home, media_gallery, media_serve, media_thumb, media_download,
    media_archive_create, media_archive_status, media_archive_download,
    media_archive_list, downloads_panel_data,
)
from core.views.permissions_view import (
    permissions_overview, role_add, role_edit, role_delete,
    user_role_list, user_role_assign,
)
from core.views.render import render_create, render_status, render_download, render_list, render_recent_json, render_delete
from core.views.settings_view import alert_settings_list, alert_settings_save
from core.views.user import user_add, user_edit, user_list, user_toggle

from django.http import JsonResponse

def health(request):
    return JsonResponse({"status": "ok"})

urlpatterns = [
    path('health/', health, name='health'),
    path('i18n/', include('django.conf.urls.i18n')),
    path('admin/', admin.site.urls),
    # REST API v1
    path('api/v1/', include('core.api_urls')),
    # auth
    path('login/', login_view, name='login'),
    path('register/', register_user, name='register'),
    path('logout/', LogoutView.as_view(http_method_names=['post', 'get']), name='logout'),
    # cameras
    path('cameras/', camera_list, name='camera_list'),
    path('cameras/add/', camera_add, name='camera_add'),
    path('cameras/<uuid:pk>/', camera_detail, name='camera_detail'),
    path('cameras/<uuid:pk>/edit/', camera_edit, name='camera_edit'),
    path('cameras/<uuid:pk>/delete/', camera_delete, name='camera_delete'),
    path('sites/add/', site_add, name='site_add'),
    # live view
    path('cameras/<uuid:pk>/live/', camera_live, name='camera_live'),
    path('cameras/<uuid:pk>/live/latest/', camera_live_latest, name='camera_live_latest'),
    path('cameras/<uuid:pk>/live/settings/', camera_live_settings, name='camera_live_settings'),
    # device management (modal)
    path('cameras/<uuid:pk>/device/', camera_device_modal, name='camera_device_modal'),
    path('cameras/<uuid:pk>/device/settings/', camera_device_settings, name='camera_device_settings'),
    path('cameras/<uuid:pk>/device/wake/', camera_device_wake, name='camera_device_wake'),
    path('cameras/<uuid:pk>/device/sim/', camera_device_sim, name='camera_device_sim'),
    path('cameras/<uuid:pk>/device/camera-settings/', camera_settings_save, name='camera_settings_save'),
    path('cameras/<uuid:pk>/device/camera-settings/pull/', camera_settings_pull, name='camera_settings_pull'),
    path('cameras/<uuid:pk>/device/state/', camera_device_state, name='camera_device_state'),
    # live view realtime (frame tu thiet bi)
    path('cameras/<uuid:pk>/live/start/', live_view_start, name='live_view_start'),
    path('cameras/<uuid:pk>/live/stop/', live_view_stop, name='live_view_stop'),
    path('cameras/<uuid:pk>/live/frame/', live_view_frame, name='live_view_frame'),
    # device API (camera -> server, khong dung session user)
    path('api/device/upload/presign/', device_api.upload_presign, name='device_upload_presign'),
    path('api/device/upload/complete/', device_api.upload_complete, name='device_upload_complete'),
    path('api/device/live/frame/', device_api.live_frame, name='device_live_frame'),
    # media
    path('media/', media_home, name='media_home'),
    path('media/camera/<uuid:camera_pk>/', media_gallery, name='media_gallery'),
    path('media/<uuid:pk>/view/', media_serve, name='media_serve'),
    path('media/<uuid:pk>/thumb/', media_thumb, name='media_thumb'),
    path('media/<uuid:pk>/download/', media_download, name='media_download'),
    # gom tải (archive ZIP nền)
    path('media/camera/<uuid:camera_pk>/archive/', media_archive_create, name='media_archive_create'),
    path('media/archive/<uuid:pk>/status/', media_archive_status, name='media_archive_status'),
    path('media/archive/<uuid:pk>/download/', media_archive_download, name='media_archive_download'),
    path('media/archives/', media_archive_list, name='media_archive_list'),
    path('media/downloads/', downloads_panel_data, name='downloads_panel_data'),
    # users
    path('users/', user_list, name='user_list'),
    path('users/add/', user_add, name='user_add'),
    path('users/<int:pk>/edit/', user_edit, name='user_edit'),
    path('users/<int:pk>/toggle/', user_toggle, name='user_toggle'),
    # clients / projects
    path('clients/', client_list, name='client_list'),
    path('clients/save/', client_save, name='client_save'),
    path('clients/add/', client_add, name='client_add'),
    path('clients/<uuid:pk>/', client_detail, name='client_detail'),
    path('clients/<uuid:pk>/edit/', client_edit, name='client_edit'),
    path('clients/<uuid:pk>/delete/', client_delete, name='client_delete'),
    # video render
    path('renders/', render_list, name='render_list'),
    path('renders/recent/', render_recent_json, name='render_recent_json'),
    path('cameras/<uuid:camera_pk>/render/', render_create, name='render_create'),
    path('renders/<uuid:pk>/status/', render_status, name='render_status'),
    path('renders/<uuid:pk>/download/', render_download, name='render_download'),
    path('renders/<uuid:pk>/delete/', render_delete, name='render_delete'),
    # permissions
    path('permissions/', permissions_overview, name='permissions_overview'),
    path('permissions/roles/add/', role_add, name='role_add'),
    path('permissions/roles/<uuid:pk>/edit/', role_edit, name='role_edit'),
    path('permissions/roles/<uuid:pk>/delete/', role_delete, name='role_delete'),
    path('permissions/users/', user_role_list, name='user_role_list'),
    path('permissions/users/<int:user_pk>/assign/', user_role_assign, name='user_role_assign'),
    # settings / alerts
    path('settings/alerts/', alert_settings_list, name='alert_settings_list'),
    path('settings/alerts/<uuid:camera_pk>/', alert_settings_save, name='alert_settings_save'),
    # home (catch-all ở cuối)
    path('dashboard/', dashboard, name='dashboard'),
    path('', index, name='home'),
    re_path(r'^.*\..*', pages, name='pages'),
]
