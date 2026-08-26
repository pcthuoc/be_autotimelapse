from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from django.test import TestCase

from core.models import Camera, CameraSchedule, Client, ClientMembership, Media, Site
from core.utils.storage import _presign_cache_key


User = get_user_model()


class TenantApiSecurityTests(TestCase):
    """Hồi quy hai lớp quyền: role hợp lệ + đúng tenant sở hữu object."""

    def setUp(self):
        self.http = APIClient()
        self.client_a = Client.objects.create(name="Tenant A")
        self.client_b = Client.objects.create(name="Tenant B")
        self.site_a = Site.objects.create(name="Site A", client=self.client_a)
        self.site_b = Site.objects.create(name="Site B", client=self.client_b)
        self.camera_a = Camera.objects.create(code="TEST-CAM-A", name="Camera A", site=self.site_a)
        self.camera_b = Camera.objects.create(code="TEST-CAM-B", name="Camera B", site=self.site_b)

        self.admin_a = User.objects.create_user(username="admin-a", password="test-pass")
        self.member_a = User.objects.create_user(username="member-a", password="test-pass")
        self.admin_b = User.objects.create_user(username="admin-b", password="test-pass")
        self.staff = User.objects.create_user(
            username="root-admin", password="test-pass", is_staff=True
        )
        ClientMembership.objects.create(
            user=self.admin_a, client=self.client_a, role=ClientMembership.Role.ADMIN
        )
        ClientMembership.objects.create(
            user=self.member_a, client=self.client_a, role=ClientMembership.Role.MEMBER
        )
        ClientMembership.objects.create(
            user=self.admin_b, client=self.client_b, role=ClientMembership.Role.ADMIN
        )

    def authenticate(self, user):
        self.http.force_authenticate(user=user)

    def test_member_can_list_camera_but_never_receives_mqtt_password(self):
        self.authenticate(self.member_a)
        response = self.http.get("/api/v1/cameras/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertNotIn("mqtt_password", response.data["results"][0])

    def test_client_admin_receives_credential_only_for_owned_camera(self):
        self.authenticate(self.admin_a)
        own = self.http.get(f"/api/v1/cameras/{self.camera_a.id}/credentials/")
        other = self.http.get(f"/api/v1/cameras/{self.camera_b.id}/credentials/")
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.data["mqtt_password"], self.camera_a.mqtt_password)
        self.assertEqual(other.status_code, 403)

    def test_client_admin_cannot_patch_or_delete_other_tenant_site(self):
        self.authenticate(self.admin_a)
        patch_response = self.http.patch(
            f"/api/v1/sites/{self.site_b.id}/", {"name": "Hijacked"}, format="json"
        )
        delete_response = self.http.delete(f"/api/v1/sites/{self.site_b.id}/")
        self.assertEqual(patch_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)
        self.site_b.refresh_from_db()
        self.assertEqual(self.site_b.name, "Site B")

    def test_client_admin_can_patch_owned_site(self):
        self.authenticate(self.admin_a)
        response = self.http.patch(
            f"/api/v1/sites/{self.site_a.id}/", {"name": "Site A Updated"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.site_a.refresh_from_db()
        self.assertEqual(self.site_a.name, "Site A Updated")

    def test_member_cannot_patch_owned_site(self):
        self.authenticate(self.member_a)
        response = self.http.patch(
            f"/api/v1/sites/{self.site_a.id}/", {"name": "Forbidden"}, format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_superadmin_can_patch_any_site(self):
        self.authenticate(self.staff)
        response = self.http.patch(
            f"/api/v1/sites/{self.site_b.id}/", {"name": "Staff Updated"}, format="json"
        )
        self.assertEqual(response.status_code, 200)


class MediaDownloadRegressionTests(TestCase):
    def setUp(self):
        self.http = APIClient()
        tenant = Client.objects.create(name="Download Tenant")
        site = Site.objects.create(name="Download Site", client=tenant)
        camera = Camera.objects.create(code="TEST-DOWNLOAD", name="Download Camera", site=site)
        self.user = User.objects.create_user(username="downloader", password="test-pass")
        ClientMembership.objects.create(
            user=self.user,
            client=tenant,
            role=ClientMembership.Role.MEMBER,
            can_download=True,
        )
        self.media = Media.objects.create(
            camera=camera,
            s3_key="",
            taken_at=timezone.now(),
            storage=Media.Storage.SEAWEED,
        )

    @patch("core.api_views.media._st.presigned_get_url", return_value="https://storage.test/file.jpg")
    def test_download_endpoint_redirects_to_presigned_storage_url(self, presign):
        self.http.force_authenticate(user=self.user)
        response = self.http.get(f"/api/v1/media/{self.media.id}/download/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://storage.test/file.jpg")
        presign.assert_called_once()


class CameraScheduleRegressionTests(TestCase):
    def setUp(self):
        self.http = APIClient()
        self.staff = User.objects.create_user(
            username="schedule-admin", password="test-pass", is_staff=True
        )
        self.camera = Camera.objects.create(code="SCHEDULE-CAM", name="Schedule Camera")
        self.http.force_authenticate(user=self.staff)

    @patch("core.api_views.cameras._push_camera_schedules")
    def test_schedule_keeps_hardware_payload_and_correct_model_metadata(self, push):
        response = self.http.post(
            f"/api/v1/cameras/{self.camera.id}/schedules/",
            {
                "name": "Ban ngày",
                "start_time": "07:15",
                "end_time": "17:45",
                "interval_sec": 300,
                "days_of_week": [1, 2, 3, 4, 5],
                "is_enabled": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        schedule = CameraSchedule.objects.get(pk=response.data["id"])
        self.assertEqual(schedule.to_dict()["start_time"], "07:15")
        self.assertEqual(CameraSchedule._meta.verbose_name, "Lịch chụp Camera")
        self.assertEqual(CameraSchedule._meta.ordering, ("start_time",))
        self.assertIn("SCHEDULE-CAM", str(schedule))
        push.assert_called_once()

    def test_schedule_rejects_invalid_hardware_values_without_500(self):
        response = self.http.post(
            f"/api/v1/cameras/{self.camera.id}/schedules/",
            {"start_time": "25:99", "interval_sec": "abc", "days_of_week": [9]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(CameraSchedule.objects.count(), 0)


class AuthCsrfRegressionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="csrf-user", password="test-pass")

    def test_login_requires_csrf_and_remember_sets_fourteen_days(self):
        no_csrf = APIClient(enforce_csrf_checks=True)
        denied = no_csrf.post(
            "/api/v1/auth/login/",
            {"username": "csrf-user", "password": "test-pass"},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

        client = APIClient(enforce_csrf_checks=True)
        csrf_response = client.get("/api/v1/auth/csrf/")
        token = csrf_response.cookies["csrftoken"].value
        allowed = client.post(
            "/api/v1/auth/login/",
            {"username": "csrf-user", "password": "test-pass", "remember": True},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertGreater(client.session.get_expiry_age(), 60 * 60 * 24 * 13)


class PresignedCacheRegressionTests(TestCase):
    def test_cache_key_separates_backend_bucket_and_response_headers(self):
        base = _presign_cache_key("same/key.jpg", 3600, storage="seaweed")
        r2 = _presign_cache_key("same/key.jpg", 3600, storage="r2")
        output = _presign_cache_key(
            "same/key.jpg", 3600, storage="r2", r2_output=True
        )
        download = _presign_cache_key(
            "same/key.jpg", 3600, storage="r2", download_name="photo.jpg"
        )
        self.assertEqual(len({base, r2, output, download}), 4)
