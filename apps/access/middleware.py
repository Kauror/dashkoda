from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse

CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'"
)
PUBLIC_PATHS = {
    "/sisene/",
    "/health/live/",
    "/health/ready/",
    "/robots.txt",
}


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if getattr(request, "viewer_access_protected", False):
            response.headers["Cache-Control"] = "private, no-store"
        return response


class ViewerAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        is_static = path.startswith(settings.STATIC_URL)
        if path in PUBLIC_PATHS or is_static:
            return self.get_response(request)

        request.viewer_access_protected = True
        authenticated = request.session.get(settings.VIEWER_SESSION_AUTHENTICATED_KEY)
        pin_version = request.session.get(settings.VIEWER_SESSION_VERSION_KEY)
        if authenticated is True and pin_version == settings.VIEWER_PIN_VERSION:
            return self.get_response(request)

        if authenticated is not None or pin_version is not None:
            request.session.flush()

        query = urlencode({"next": request.get_full_path()})
        login_url = f"{reverse('viewer-login')}?{query}"

        # HTMX follows redirects inside the XHR, which would swap the login page
        # into a fragment target. HX-Redirect makes the browser navigate instead.
        # The route policy is unchanged: HTMX routes stay protected and are never
        # added to the public allowlist.
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response.headers["HX-Redirect"] = login_url
            return response

        return HttpResponseRedirect(login_url)
