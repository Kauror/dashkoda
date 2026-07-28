from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .client import client_key
from .forms import ViewerLoginForm
from .rate_limit import check_pin


def _safe_next(request) -> str:
    candidate = request.POST.get("next") or request.GET.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return reverse("home")


@require_http_methods(["GET", "POST"])
def login_view(request):
    if (
        request.session.get(settings.VIEWER_SESSION_AUTHENTICATED_KEY) is True
        and request.session.get(settings.VIEWER_SESSION_VERSION_KEY) == settings.VIEWER_PIN_VERSION
    ):
        return redirect(_safe_next(request))

    form = ViewerLoginForm(request.POST or None)
    status = 200
    retry_after = None
    if request.method == "POST" and form.is_valid():
        result = check_pin(client_key(request), form.cleaned_data["pin"])
        if result.authenticated:
            request.session.cycle_key()
            request.session[settings.VIEWER_SESSION_AUTHENTICATED_KEY] = True
            request.session[settings.VIEWER_SESSION_VERSION_KEY] = settings.VIEWER_PIN_VERSION
            request.session.set_expiry(settings.SESSION_COOKIE_AGE)
            return redirect(_safe_next(request))
        if result.locked:
            status = 429
            retry_after = result.retry_after
            form.add_error(None, "Liiga palju katseid. Proovi hiljem uuesti.")
        else:
            form.add_error(None, "PIN-kood ei ole õige.")

    response = render(
        request,
        "access/login.html",
        {"form": form, "next": _safe_next(request)},
        status=status,
    )
    response.headers["Cache-Control"] = "private, no-store"
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


@require_POST
def logout_view(request):
    request.session.flush()
    return redirect("viewer-login")


@require_GET
def home(request):
    return render(request, "access/home.html")


@require_GET
def robots(_request):
    return HttpResponse(
        "User-agent: *\nDisallow: /\n",
        content_type="text/plain; charset=utf-8",
    )
