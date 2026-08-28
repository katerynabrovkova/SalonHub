from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("accounts.urls")),
    path("api/v1/webhooks/", include("payments.urls")),
    path("api/v1/salons/<slug:slug>/", include("accounts.salon_urls")),
    path("api/v1/salons/<slug:slug>/", include("booking.urls")),
    path("api/v1/salons/<slug:slug>/", include("catalog.urls")),
    path("api/v1/salons/<slug:slug>/", include("specialists.urls")),
    path("api/v1/salons/<slug:slug>/", include("scheduling.urls")),
]
