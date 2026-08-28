"""
Salon-prefixed auth routes (docs/DECISIONS.md § Stage 3-R.D.3, § Stage
3-R.D.4). Included at ``/api/v1/salons/<slug>/`` alongside the domain apps;
distinct app_name because ``accounts.urls`` (flat ``/api/v1/auth/``) already
owns "accounts".

Registration and email-verification for now — password-reset and
resend-verification (3-R.D.5) land here next; login/refresh/logout relocate
here in 3-R.E.
"""

from django.urls import path

from accounts import views

app_name = "salon_auth"

urlpatterns = [
    path("auth/register/", views.RegisterView.as_view(), name="register"),
    path("auth/verify-email/", views.VerifyEmailView.as_view(), name="verify-email"),
]
