from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET


def _status_response(status: str, *, http_status: int = 200) -> JsonResponse:
    return JsonResponse(
        {"status": status},
        status=http_status,
        json_dumps_params={"separators": (",", ":")},
    )


@require_GET
def liveness(_request):
    return _status_response("ok")


@require_GET
def readiness(_request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return _status_response("unavailable", http_status=503)

    return _status_response("ok")
