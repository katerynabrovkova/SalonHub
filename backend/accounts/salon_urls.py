"""
Salon-prefixed auth routes (docs/DECISIONS.md § Stage 3-R.D.3, § Stage
3-R.D.4, § Stage 3-R.E). Included at ``/api/v1/salons/<slug>/`` alongside
the domain apps. This is now the only auth surface — the flat
``/api/v1/auth/`` prefix (``accounts.urls``) was removed in 3-R.E, so
``app_name = "salon_auth"`` no longer needs to stay distinct from anything.

Registration, email-verification, password-reset and resend-verification
(3-R.D); login, refresh and logout against ``Account`` (3-R.E).
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
    path("auth/csrf/", views.AuthCsrfView.as_view(), name="csrf"),
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("auth/refresh/", views.RefreshView.as_view(), name="refresh"),
    path("auth/logout/", views.LogoutView.as_view(), name="logout"),
]
