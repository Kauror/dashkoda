from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def liveness(_request):
    return JsonResponse({"status": "ok"})
