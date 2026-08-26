import re

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# Only ASCII safe chars for username — prevents diacritic/homograph confusion
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9@.+_-]+$")


class LoginForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={
            "placeholder": _("Username"),
            "class": "form-control",
            "autofocus": True,
            "autocomplete": "username",
        }),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            "placeholder": _("Password"),
            "class": "form-control",
            "autocomplete": "current-password",
        }),
    )

    def clean_username(self):
        return self.cleaned_data["username"].strip()


class SignUpForm(UserCreationForm):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={
            "placeholder": _("Username"),
            "class": "form-control",
            "autofocus": True,
            "autocomplete": "username",
        }),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            "placeholder": _("Email"),
            "class": "form-control",
            "autocomplete": "email",
        }),
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            "placeholder": _("Password (min. 8 characters)"),
            "class": "form-control",
            "autocomplete": "new-password",
        }),
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            "placeholder": _("Confirm password"),
            "class": "form-control",
            "autocomplete": "new-password",
        }),
    )
    agree_to_terms = forms.BooleanField(
        required=True,
        error_messages={"required": _("You must accept the terms and conditions.")},
    )

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if not _USERNAME_RE.match(username):
            raise ValidationError(
                _("Username may only contain letters, digits and: @ . + - _")
            )
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError(_("This username is already taken."))
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("This email is already registered."))
        return email


# ── Camera / Site forms ───────────────────────────────────────────────────────

from core.models.camera import Camera, Site


class SiteForm(forms.ModelForm):
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": _("Site name"),
        }),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": _("Description (optional)"),
        }),
    )

    class Meta:
        model = Site
        fields = ("name", "description")


class CameraForm(forms.ModelForm):
    code = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": _("Auto-generated if blank"),
        }),
    )
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": _("Display name"),
        }),
    )
    site = forms.ModelChoiceField(
        queryset=Site.objects.all().order_by("name"),
        required=False,
        empty_label=_("— Select site —"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    timezone = forms.CharField(
        max_length=50,
        initial="Asia/Ho_Chi_Minh",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g. Asia/Ho_Chi_Minh",
        }),
    )
    camera_model = forms.ChoiceField(
        choices=Camera.Model.choices,
        initial=Camera.Model.GENERIC,
        widget=forms.Select(attrs={"class": "form-select"}),
        label=_("Camera model"),
    )

    class Meta:
        model = Camera
        fields = ("code", "name", "site", "timezone", "camera_model")

    def clean_code(self):
        code = self.cleaned_data.get("code", "").strip().upper()
        if not code:
            return ""  # model.save() sẽ tự tạo
        qs = Camera.objects.filter(code__iexact=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(_("This camera code already exists."))
        return code


# ── User management forms ─────────────────────────────────────────────────────

from django.contrib.auth.forms import UserCreationForm as _BaseCreate
from core.models.permission import Role, UserRole


class UserCreateForm(_BaseCreate):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": _("Username")}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": _("Email")}),
    )
    password1 = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": _("Password")}),
    )
    password2 = forms.CharField(
        label=_("Confirm password"),
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": _("Confirm password")}),
    )
    role = forms.ModelChoiceField(
        queryset=Role.objects.all().order_by("name"),
        required=False,
        empty_label=_("— No role —"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if not _USERNAME_RE.match(username):
            raise ValidationError(
                _("Username may only contain letters, digits and: @ . + - _")
            )
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError(_("This username is already taken."))
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("This email is already registered."))
        return email

    def save(self, commit=True):
        user = super().save(commit=commit)
        role = self.cleaned_data.get("role")
        if role and commit:
            UserRole.objects.get_or_create(user=user, role=role)
        return user


class UserEditForm(forms.ModelForm):
    role = forms.ModelChoiceField(
        queryset=Role.objects.all().order_by("name"),
        required=False,
        empty_label=_("— No role —"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    is_active = forms.BooleanField(
        required=False,
        label=_("Active account"),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    class Meta:
        model = User
        fields = ("is_active",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            ur = UserRole.objects.filter(user=self.instance).select_related("role").first()
            self.fields["role"].initial = ur.role if ur else None

    def save(self, commit=True):
        user = super().save(commit=commit)
        role = self.cleaned_data.get("role")
        if commit:
            UserRole.objects.filter(user=user).delete()
            if role:
                UserRole.objects.create(user=user, role=role)
        return user





# ── Camera device settings form ───────────────────────────────────────────────

from core.models.camera import CameraDevice


class CameraDeviceSettingsForm(forms.ModelForm):
    """Chỉnh thông số vận hành cơ bản của thiết bị từ modal."""

    capture_interval_sec = forms.IntegerField(
        label=_("Capture interval (seconds)"),
        min_value=30,
        max_value=86400,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "step": 30,
        }),
    )

    class Meta:
        model = CameraDevice
        fields = ("capture_interval_sec",)


# ── Camera imaging settings form (Nikon D5300 contract) ───────────────────────

from core.models.camera import CameraSettings


class CameraSettingsForm(forms.Form):
    """
    Form camera settings — driven bởi camera profile (camera_specs.py).

    Tất cả giá trị là chuỗi native gphoto2 (không alias).
    JS post JSON; view validate và save thủ công vào model.
    """

    def __init__(self, *args, camera_model="generic", capabilities=None,
                 instance=None, **kwargs):
        # Khởi tạo initial values từ model instance nếu có
        if instance is not None and "initial" not in kwargs:
            from core.camera_specs import get_spec as _gs
            _settable = _gs(camera_model).get("settable", [])
            kwargs["initial"] = {
                f: getattr(instance, f, "") for f in _settable
            }
        super().__init__(*args, **kwargs)
        from core.camera_specs import get_spec
        spec = get_spec(camera_model)
        caps = capabilities or {}

        for field_name in spec.get("settable", []):
            fdef = spec["fields"].get(field_name, {})
            label = fdef.get("label", field_name)
            note = fdef.get("note", "")
            # Choices: ưu tiên capabilities từ camera thực tế, fallback về spec
            choices = caps.get(field_name, {}).get("choices") or fdef.get("choices", [])

            if fdef.get("type") == "TEXT" or not choices:
                self.fields[field_name] = forms.CharField(
                    required=False,
                    label=label,
                    widget=forms.TextInput(attrs={
                        "class": "form-control form-control-sm",
                        "placeholder": label,
                        "title": note,
                    }),
                )
            else:
                submitted_data = args[0] if args and isinstance(args[0], dict) else {}
                submitted_val = submitted_data.get(field_name) or (instance and getattr(instance, field_name, None))
                all_choices = list(choices)
                if submitted_val and str(submitted_val) not in all_choices:
                    all_choices.append(str(submitted_val))
                self.fields[field_name] = forms.ChoiceField(
                    choices=[("", "—")] + [(str(c), str(c)) for c in all_choices],
                    required=False,
                    label=label,
                    widget=forms.Select(attrs={
                        "class": "form-select form-select-sm",
                        "title": note,
                    }),
                )
        self.spec = spec
        self.camera_model = camera_model

    def save_to(self, cam_settings):
        """Ghi giá trị validated vào CameraSettings instance (không commit)."""
        for field_name, value in self.cleaned_data.items():
            if hasattr(cam_settings, field_name):
                setattr(cam_settings, field_name, value)
        return cam_settings
