from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
from django.template import loader
from django.urls import reverse
from django import template


@login_required(login_url="/login/")
def index(request):
    return HttpResponseRedirect(reverse("dashboard"))


@login_required(login_url="/login/")
def pages(request):
    context = {}
    try:
        load_template = request.path.split("/")[-1]
        if load_template == "admin":
            return HttpResponseRedirect(reverse("admin:index"))
        context["segment"] = load_template
        html_template = loader.get_template("home/" + load_template)
        return HttpResponse(html_template.render(context, request))
    except template.TemplateDoesNotExist:
        html_template = loader.get_template("home/page-404.html")
        return HttpResponse(html_template.render(context, request))
    except Exception:
        html_template = loader.get_template("home/page-500.html")
        return HttpResponse(html_template.render(context, request))
