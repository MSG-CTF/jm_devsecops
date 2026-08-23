from django.http import JsonResponse


def healthz(request):
    """프로세스가 HTTP 요청에 응답할 수 있는지 확인한다."""
    return JsonResponse({"status": "ok"})
