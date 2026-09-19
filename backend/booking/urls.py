from django.urls import path

from booking import views

app_name = "booking"

urlpatterns = [
    path(
        "bookings/",
        views.GuestBookingCreateView.as_view(),
        name="guest-booking-create",
    ),
    path(
        "guest/appointments/<int:appointment_id>/",
        views.GuestAppointmentDetailView.as_view(),
        name="guest-appointment-detail",
    ),
    path(
        "guest/appointments/<int:appointment_id>/cancel/",
        views.GuestAppointmentCancelView.as_view(),
        name="guest-appointment-cancel",
    ),
    path(
        "guest/appointments/<int:appointment_id>/pay/",
        views.GuestAppointmentPayView.as_view(),
        name="guest-appointment-pay",
    ),
    path(
        "appointments/mine/",
        views.AccountAppointmentListView.as_view(),
        name="account-appointment-list",
    ),
    path(
        "appointments/<int:appointment_id>/cancel/",
        views.AccountAppointmentCancelView.as_view(),
        name="account-appointment-cancel",
    ),
]
