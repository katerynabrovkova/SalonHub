from django.urls import path

from reviews import views

app_name = "reviews"

urlpatterns = [
    path(
        "appointments/<int:appointment_id>/review/",
        views.ReviewCreateView.as_view(),
        name="review-create",
    ),
]
