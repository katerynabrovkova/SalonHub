"""
Salon-prefixed auth routes (docs/DECISIONS.md § Stage 3-R.D.3). Included at
``/api/v1/salons/<slug>/`` alongside the domain apps; distinct app_name
because ``accounts.urls`` (flat ``/api/v1/auth/``) already owns "accounts".

Only registration for now — email-verification (3-R.D.4) and password-reset
(3-R.D.5) land here next; login/refresh/logout relocate here in 3-R.E.
"""

from django.urls import path

from accounts import views

app_name = "salon_auth"

urlpatterns = [
    path("auth/register/", views.RegisterView.as_view(), name="register"),
]
