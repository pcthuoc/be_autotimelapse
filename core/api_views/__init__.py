# Re-export tất cả views để api_urls.py không cần thay đổi gì
from .auth import api_login, api_logout, api_me
from .dashboard import api_dashboard
from .cameras import (
    api_cameras, api_camera_detail, api_camera_live_latest,
    api_camera_live_start, api_camera_live_stop, api_camera_live_frame,
    api_camera_device, api_camera_device_update,
    api_camera_credentials, api_camera_simconfig,
    api_camera_power_on_cm4, api_camera_power_off_cm4,
    api_camera_mqtt_register, api_camera_settings,
)
from .sites import api_sites, api_site_detail, api_site_assign_client
from .media import api_media_gallery, api_media_delete, api_media_bulk_delete, api_media_download
from .renders import api_renders, api_render_create, api_render_detail
from .downloads import api_archive_create, api_archive_detail, api_downloads
from .clients import api_clients, api_client_detail, api_client_members, api_client_member_detail
from .users import api_users, api_user_detail, api_user_search, api_roles, api_user_toggle
from .alerts import api_alert_settings, api_alert_settings_save
from .storage import api_storage_stats

__all__ = [
    'api_login', 'api_logout', 'api_me',
    'api_dashboard',
    'api_cameras', 'api_camera_detail', 'api_camera_live_latest',
    'api_camera_live_start', 'api_camera_live_stop', 'api_camera_live_frame',
    'api_camera_device', 'api_camera_device_update',
    'api_camera_credentials', 'api_camera_simconfig',
    'api_camera_power_on_cm4', 'api_camera_power_off_cm4',
    'api_camera_mqtt_register', 'api_camera_settings',
    'api_sites', 'api_site_detail', 'api_site_assign_client',
    'api_media_gallery', 'api_media_delete', 'api_media_bulk_delete', 'api_media_download',
    'api_renders', 'api_render_create', 'api_render_detail',
    'api_archive_create', 'api_archive_detail', 'api_downloads',
    'api_clients', 'api_client_detail', 'api_client_members', 'api_client_member_detail',
    'api_users', 'api_user_detail', 'api_user_search', 'api_roles', 'api_user_toggle',
    'api_alert_settings', 'api_alert_settings_save',
    'api_storage_stats',
]
