from django.urls import path

from tenants import views

app_name = "tenants"

urlpatterns = [
    path("", views.SalonInfoDetailView.as_view(), name="salon-detail"),
]
