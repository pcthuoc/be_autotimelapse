from django.contrib.auth import authenticate, login
from django.shortcuts import render, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from core.forms import LoginForm, SignUpForm

_SESSION_REMEMBER_AGE = 60 * 60 * 24 * 14   # 14 ngày


def _safe_next(request):
    next_url = request.POST.get("next") or request.GET.get("next") or "/cameras/"
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return "/"
    return next_url


def login_view(request):
    msg = None

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data["username"]
            password = form.cleaned_data["password"]
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                if request.POST.get("remember"):
                    request.session.set_expiry(_SESSION_REMEMBER_AGE)
                else:
                    request.session.set_expiry(0)
                return redirect(_safe_next(request))
            else:
                msg = _("Invalid username or password.")
        else:
            msg = _("Please check your login details.")
    else:
        form = LoginForm()

    return render(request, "accounts/login.html", {"form": form, "msg": msg})


def register_user(request):
    msg = None
    success = False

    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            # Tài khoản tự đăng ký cần admin kích hoạt trước khi đăng nhập được
            user.is_active = False
            user.save()
            msg = _("Account created! Please wait for admin approval before logging in.")
            success = True
        else:
            msg = _("Please check your registration details.")
    else:
        form = SignUpForm()

    return render(request, "accounts/register.html", {
        "form": form,
        "msg": msg,
        "success": success,
    })
