from django.urls import path
from . import api_views

urlpatterns = [
    # Auth
    path('auth/login/',  api_views.api_login),
    path('auth/logout/', api_views.api_logout),
    path('auth/me/',     api_views.api_me),

    # Dashboard
    path('dashboard/', api_views.api_dashboard),

    # Cameras
    path('cameras/',                             api_views.api_cameras),
    path('cameras/<uuid:pk>/',                   api_views.api_camera_detail),
    path('cameras/<uuid:pk>/live/latest/',       api_views.api_camera_live_latest),
    path('cameras/<uuid:pk>/live/start/',        api_views.api_camera_live_start),
    path('cameras/<uuid:pk>/live/stop/',         api_views.api_camera_live_stop),
    path('cameras/<uuid:pk>/live/frame/',        api_views.api_camera_live_frame),
    path('cameras/<uuid:pk>/device/',            api_views.api_camera_device),
    path('cameras/<uuid:pk>/device/update/',     api_views.api_camera_device_update),
    path('cameras/<uuid:pk>/camera-settings/',   api_views.api_camera_settings),
    path('cameras/<uuid:pk>/credentials/',       api_views.api_camera_credentials),
    path('cameras/<uuid:pk>/simconfig/',         api_views.api_camera_simconfig),
    path('cameras/<uuid:pk>/mqtt-register/',     api_views.api_camera_mqtt_register),
    path('cameras/<uuid:pk>/power-on-cm4/',      api_views.api_camera_power_on_cm4),
    path('cameras/<uuid:pk>/power-off-cm4/',     api_views.api_camera_power_off_cm4),

    # Sites
    path('sites/',                               api_views.api_sites),
    path('sites/<uuid:pk>/',                     api_views.api_site_detail),
    path('sites/<uuid:pk>/assign-client/',       api_views.api_site_assign_client),

    # Media
    path('media/camera/<uuid:camera_pk>/',          api_views.api_media_gallery),
    path('media/camera/<uuid:camera_pk>/archive/',  api_views.api_archive_create),
    path('media/<uuid:pk>/',                        api_views.api_media_delete),
    path('media/<uuid:pk>/download/',               api_views.api_media_download),
    path('media/bulk-delete/',                      api_views.api_media_bulk_delete),

    # Renders
    path('renders/',                             api_views.api_renders),
    path('renders/<uuid:pk>/',                   api_views.api_render_detail),
    path('cameras/<uuid:camera_pk>/render/',     api_views.api_render_create),

    # Archives
    path('archives/<uuid:pk>/',                  api_views.api_archive_detail),

    # Downloads center
    path('downloads/',                           api_views.api_downloads),

    # Clients
    path('clients/',                             api_views.api_clients),
    path('clients/<uuid:pk>/',                   api_views.api_client_detail),
    path('clients/<uuid:pk>/members/',           api_views.api_client_members),
    path('clients/<uuid:pk>/members/<int:user_id>/', api_views.api_client_member_detail),

    # Users
    path('users/',                               api_views.api_users),
    path('users/search/',                        api_views.api_user_search),
    path('users/<int:pk>/toggle/',               api_views.api_user_toggle),
    path('users/<int:pk>/',                      api_views.api_user_detail),
    path('roles/',                               api_views.api_roles),

    # Alert settings
    path('settings/alert/',                      api_views.api_alert_settings),

    # Storage stats (admin only)
    path('storage/stats/',                       api_views.api_storage_stats),
    path('settings/alert/<uuid:camera_pk>/',     api_views.api_alert_settings_save),
]
