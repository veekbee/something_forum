from django.http import HttpResponse
from django.views.decorators.http import require_GET


@require_GET
def robots_txt(request):
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")
