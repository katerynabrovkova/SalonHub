"""
Salon-prefixed auth routes (docs/DECISIONS.md § Stage 3-R.D.3, § Stage
3-R.D.4). Included at ``/api/v1/salons/<slug>/`` alongside the domain apps;
distinct app_name because ``accounts.urls`` (flat ``/api/v1/auth/``) already
owns "accounts".

Registration, email-verification, password-reset and resend-verification;
login/refresh/logout relocate here in 3-R.E.
"""

from django.urls import path

from accounts import views

app_name = "salon_auth"

urlpatterns = [
    path("auth/register/", views.RegisterView.as_view(), name="register"),
    path("auth/verify-email/", views.VerifyEmailView.as_view(), name="verify-email"),
    path(
        "auth/password-reset/",
        views.PasswordResetRequestView.as_view(),
        name="password-reset",
    ),
    path(
        "auth/password-reset/confirm/",
        views.PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path(
        "auth/resend-verification/",
        views.ResendVerificationView.as_view(),
        name="resend-verification",
    ),
]
