from django.conf import settings


def company(request):
    return {
        "COMPANY_NAME": settings.COMPANY_NAME,
        "COMPANY_ADDRESS": settings.COMPANY_ADDRESS,
        "KVKK_EMAIL": settings.KVKK_EMAIL,
    }
