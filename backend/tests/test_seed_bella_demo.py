"""
seed_bella_demo: fills the demo salon (slug "bella-demo") from the tables in
tenants/demo/bella_demo.py (docs/DECISIONS.md § "Stage 17 sub-step order",
17.8 "Demo salon commands"). Creates or updates only, never deletes, never
touches another salon, overwrites the seeded fields on every run, and runs
only with DEBUG on.

The seed takes `now` explicitly; these tests pass a frozen one and never
compare it with the real clock.
"""

import datetime as dt
from io import StringIO

import pytest
from django.core import mail
from django.core.management import CommandError, call_command

from accounts.models import Customer
from booking.models import Appointment, AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from notifications.models import Notification
from reviews.models import Review
from specialists.models import Specialist, SpecialistService, WorkingHours
from specialists.services import annotate_rating
from tenants.demo import bella_demo as demo
from tenants.models import FAQItem, Salon

NOW = dt.datetime(2026, 11, 9, 12, 0, tzinfo=dt.UTC)

SEEDED_MODELS = [
    ServiceCategory,
    Service,
    Specialist,
    SpecialistService,
    WorkingHours,
    FAQItem,
    Customer,
    Appointment,
    Review,
]


@pytest.fixture(autouse=True)
def _debug_on(settings):
    settings.DEBUG = True


def _rows(model, salon_id):
    return sorted(
        model.unscoped_objects.filter(salon_id=salon_id).values(), key=lambda row: row["id"]
    )


def _pks(model, salon_id):
    return set(model.unscoped_objects.filter(salon_id=salon_id).values_list("pk", flat=True))


def _seed():
    return demo.seed_bella_demo(now=NOW)


def _service(salon, en):
    with tenant_context(salon.id):
        return Service.objects.get(salon=salon, name__en=en)


def _specialist(salon, en):
    with tenant_context(salon.id):
        return Specialist.objects.get(salon=salon, name__en=en)


# --- guards -------------------------------------------------------------------


def test_refuses_when_debug_is_off(settings, salon):
    settings.DEBUG = False

    with pytest.raises(CommandError, match="DEBUG"):
        call_command("seed_bella_demo", stdout=StringIO())

    assert _pks(ServiceCategory, salon.id) == set()
    assert _pks(Service, salon.id) == set()


def test_refuses_when_there_is_no_bella_demo_salon(other_salon):
    with pytest.raises(CommandError, match="bella-demo"):
        call_command("seed_bella_demo", stdout=StringIO())

    for model in SEEDED_MODELS:
        assert _pks(model, other_salon.id) == set()


def test_command_seeds_the_salon(salon):
    out = StringIO()

    call_command("seed_bella_demo", stdout=out)

    assert len(_pks(Service, salon.id)) == len(demo.SERVICES)
    assert "bella-demo" in out.getvalue()


# --- content ------------------------------------------------------------------


def test_creates_every_row_from_the_tables(salon):
    _seed()

    with tenant_context(salon.id):
        categories = {c.name["en"]: c for c in ServiceCategory.objects.filter(salon=salon)}
        services = {s.name["en"]: s for s in Service.objects.filter(salon=salon)}
        specialists = {s.name["en"]: s for s in Specialist.objects.filter(salon=salon)}
        faq = list(FAQItem.objects.filter(salon=salon).order_by("ordering"))

        assert {c.en for c in demo.CATEGORIES} == set(categories)
        for row in demo.CATEGORIES:
            assert categories[row.en].name == {"uk": row.uk, "en": row.en}

        assert {s.en for s in demo.SERVICES} == set(services)
        category_en = {c.key: c.en for c in demo.CATEGORIES}
        specialist_en = {s.key: s.en for s in demo.SPECIALISTS}
        for row in demo.SERVICES:
            service = services[row.en]
            assert service.name == {"uk": row.uk, "en": row.en}
            assert service.description == row.description
            assert service.duration_minutes == row.duration_minutes
            assert service.buffer_minutes == row.buffer_minutes
            assert str(service.price) == row.price
            assert service.category.name["en"] == category_en[row.category]
            assert service.is_active
            performers = set(
                SpecialistService.objects.filter(salon=salon, service=service).values_list(
                    "specialist__name__en", flat=True
                )
            )
            assert performers == {specialist_en[key] for key in row.specialists}

        assert {s.en for s in demo.SPECIALISTS} == set(specialists)
        for row in demo.SPECIALISTS:
            specialist = specialists[row.en]
            assert specialist.name == {"uk": row.uk, "en": row.en}
            assert specialist.bio == {"uk": row.bio_uk, "en": row.bio_en}
            hours = sorted(
                (h.day_of_week, h.start_time.strftime("%H:%M"), h.end_time.strftime("%H:%M"))
                for h in WorkingHours.objects.filter(salon=salon, specialist=specialist)
            )
            assert hours == sorted(row.hours)

        assert [(f.question, f.answer) for f in faq] == [
            (row.question, row.answer) for row in demo.FAQ
        ]
        assert all(f.is_active for f in faq)


def test_sets_the_salon_phone_and_address(salon, other_salon):
    other_before = Salon.objects.filter(pk=other_salon.pk).values().get()

    _seed()

    salon.refresh_from_db()
    assert salon.phone == demo.PLACEHOLDER_PHONE
    assert salon.address == demo.ADDRESS
    assert Salon.objects.filter(pk=other_salon.pk).values().get() == other_before


def test_second_run_creates_no_duplicates_and_keeps_every_pk(salon):
    _seed()
    first = {model.__name__: _pks(model, salon.id) for model in SEEDED_MODELS}

    _seed()

    assert {model.__name__: _pks(model, salon.id) for model in SEEDED_MODELS} == first


def test_overwrites_seeded_fields_on_every_run(salon):
    _seed()
    row = demo.SERVICES[0]
    faq_row = demo.FAQ[0]
    with tenant_context(salon.id):
        Service.objects.filter(salon=salon, name__en=row.en).update(price="999.00")
        FAQItem.objects.filter(salon=salon, question=faq_row.question).update(answer="Змінено.")

    _seed()

    assert str(_service(salon, row.en).price) == row.price
    with tenant_context(salon.id):
        assert FAQItem.objects.get(salon=salon, question=faq_row.question).answer == faq_row.answer


def test_updates_existing_rows_in_place(salon, customer):
    """The live salon already has categories, services 1-3 (with past
    appointments), Anna with her hours, and five FAQ items with typos."""
    with tenant_context(salon.id):
        manicure = ServiceCategory.objects.create(salon=salon, name={"en": "Manicure"})
        old_service = Service.objects.create(
            salon=salon,
            category=manicure,
            name={"en": "coating and reinforcement"},
            duration_minutes=60,
            price="10.00",
        )
        anna = Specialist.objects.create(salon=salon, name={"en": "Anna"}, bio={"en": "old"})
        SpecialistService.objects.create(salon=salon, specialist=anna, service=old_service)
        anna_hours = WorkingHours.objects.create(
            salon=salon,
            specialist=anna,
            day_of_week=0,
            start_time=dt.time(9, 0),
            end_time=dt.time(18, 0),
        )
        old_faq = FAQItem.objects.create(
            salon=salon, question="Чи є у вас шось поїсти?", answer="Так."
        )
        appointment = Appointment.objects.create(
            salon=salon,
            customer=customer,
            specialist=anna,
            service=old_service,
            start_datetime=NOW - dt.timedelta(days=30),
            end_datetime=NOW - dt.timedelta(days=30) + dt.timedelta(hours=1),
            blocked_until=NOW - dt.timedelta(days=30) + dt.timedelta(hours=1),
            service_price_at_booking="10.00",
            deposit_percentage_at_booking="20.00",
            hold_expires_at=NOW - dt.timedelta(days=30),
            status=AppointmentStatus.EXPIRED,
        )

    _seed()

    new_service_row = next(r for r in demo.SERVICES if "coating and reinforcement" in r.previous_en)
    anna_row = next(r for r in demo.SPECIALISTS if r.en == "Anna")
    faq_row = next(r for r in demo.FAQ if "Чи є у вас шось поїсти?" in r.previous_questions)
    with tenant_context(salon.id):
        manicure.refresh_from_db()
        old_service.refresh_from_db()
        anna.refresh_from_db()
        old_faq.refresh_from_db()
        appointment.refresh_from_db()

        assert manicure.name["uk"] == "Манікюр"
        assert old_service.name == {"uk": new_service_row.uk, "en": new_service_row.en}
        assert old_service.duration_minutes == new_service_row.duration_minutes
        assert str(old_service.price) == new_service_row.price
        assert anna.bio == {"uk": anna_row.bio_uk, "en": anna_row.bio_en}
        assert list(WorkingHours.objects.filter(salon=salon, specialist=anna)) == [anna_hours]
        assert (old_faq.question, old_faq.answer) == (faq_row.question, faq_row.answer)
        assert appointment.service_id == old_service.pk
        assert ServiceCategory.objects.filter(salon=salon, name__en="Manicure").count() == 1
        assert Specialist.objects.filter(salon=salon, name__en="Anna").count() == 1
        assert FAQItem.objects.filter(salon=salon, question=faq_row.question).count() == 1


def test_never_deletes_rows_missing_from_the_tables(salon):
    with tenant_context(salon.id):
        extra_category = ServiceCategory.objects.create(salon=salon, name={"en": "Extra"})
        extra_service = Service.objects.create(
            salon=salon,
            category=extra_category,
            name={"en": "Extra service"},
            duration_minutes=30,
            price="5.00",
        )
        olena = Specialist.objects.create(salon=salon, name={"en": "Olena"})
        extra_link = SpecialistService.objects.create(
            salon=salon, specialist=olena, service=extra_service
        )
        sunday = WorkingHours.objects.create(
            salon=salon,
            specialist=olena,
            day_of_week=6,
            start_time=dt.time(10, 0),
            end_time=dt.time(12, 0),
        )
        extra_faq = FAQItem.objects.create(salon=salon, question="Інше питання?", answer="Так.")

    _seed()

    with tenant_context(salon.id):
        assert ServiceCategory.objects.filter(pk=extra_category.pk).exists()
        assert Service.objects.filter(pk=extra_service.pk).exists()
        assert SpecialistService.objects.filter(pk=extra_link.pk).exists()
        assert FAQItem.objects.filter(pk=extra_faq.pk).exists()
        # Hours are created only for a specialist who has none, so Olena's
        # existing row is neither replaced nor joined by the table's rows.
        assert list(WorkingHours.objects.filter(salon=salon, specialist=olena)) == [sunday]


def test_never_touches_another_salon(salon, other_salon):
    """The other salon is created first, so its rows have the lower pks and
    any lookup that drops the salon filter finds them before bella-demo's."""
    with tenant_context(other_salon.id):
        category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Manicure"})
        service = Service.objects.create(
            salon=other_salon,
            category=category,
            name={"en": "coating and reinforcement"},
            duration_minutes=60,
            price="10.00",
        )
        anna = Specialist.objects.create(salon=other_salon, name={"en": "Anna"})
        SpecialistService.objects.create(salon=other_salon, specialist=anna, service=service)
        FAQItem.objects.create(
            salon=other_salon, question="Де можна припаркуватись?", answer="Так."
        )
        Customer.objects.create(
            salon=other_salon,
            name="Other",
            email="demo-review-01@example.com",
            phone="+10000000000",
        )
    before = {model.__name__: _rows(model, other_salon.id) for model in SEEDED_MODELS}

    _seed()
    _seed()

    assert {model.__name__: _rows(model, other_salon.id) for model in SEEDED_MODELS} == before
    assert len(_pks(Service, salon.id)) == len(demo.SERVICES)


# --- demo reviews -------------------------------------------------------------


def test_creates_the_demo_reviews_with_the_planned_ratings(salon):
    _seed()

    specialist_en = {s.key: s.en for s in demo.SPECIALISTS}
    with tenant_context(salon.id):
        ratings = {
            s.name["en"]: (s.average_rating, s.review_count)
            for s in annotate_rating(Specialist.objects.filter(salon=salon))
        }
    for row in demo.SPECIALISTS:
        planned = [r.rating for r in demo.REVIEWS if r.specialist == row.key]
        if planned:
            assert ratings[row.en][1] == len(planned)
            assert ratings[row.en][0] == pytest.approx(sum(planned) / len(planned))
        else:
            assert ratings[row.en] == (None, 0)
    assert ratings[specialist_en["sofiia"]] == (None, 0)


def test_demo_reviews_are_marked_and_tied_to_completed_past_visits(salon):
    _seed()

    with tenant_context(salon.id):
        reviews = list(
            Review.objects.filter(salon=salon).select_related(
                "appointment", "customer", "specialist"
            )
        )
        assert len(reviews) == len(demo.REVIEWS)
        for review in reviews:
            appointment = review.appointment
            assert review.customer.email.endswith("@example.com")
            assert review.customer.email.startswith("demo-review-")
            assert review.customer.name.startswith("Демо-клієнтка")
            assert appointment.customer_id == review.customer_id
            assert appointment.specialist_id == review.specialist_id
            assert appointment.status == AppointmentStatus.COMPLETED
            assert appointment.end_datetime < NOW
            assert review.created_at > appointment.end_datetime
            assert review.created_at < NOW
            assert SpecialistService.objects.filter(
                salon=salon, specialist=review.specialist, service=appointment.service
            ).exists()
        assert Customer.objects.filter(salon=salon).count() == len(demo.REVIEWS)


def test_demo_visits_fall_inside_the_specialists_working_hours(salon):
    _seed()

    with tenant_context(salon.id):
        for appointment in Appointment.objects.filter(salon=salon).select_related("salon"):
            local_start = appointment.start_datetime.astimezone(demo.salon_zone(salon))
            local_end = appointment.end_datetime.astimezone(demo.salon_zone(salon))
            ranges = WorkingHours.objects.filter(
                salon=salon,
                specialist=appointment.specialist,
                day_of_week=local_start.weekday(),
            )
            assert any(
                r.start_time <= local_start.time() and local_end.time() <= r.end_time
                for r in ranges
            )


def test_sends_nothing(salon):
    _seed()

    with tenant_context(salon.id):
        assert Notification.objects.filter(salon=salon).count() == 0
    assert mail.outbox == []


# --- the tables themselves ----------------------------------------------------


def test_tables_are_consistent():
    category_keys = {c.key for c in demo.CATEGORIES}
    specialist_keys = {s.key for s in demo.SPECIALISTS}

    for names in (
        [c.uk for c in demo.CATEGORIES],
        [c.en for c in demo.CATEGORIES],
        [s.uk for s in demo.SERVICES],
        [s.en for s in demo.SERVICES],
        [s.en for s in demo.SPECIALISTS],
        [f.question for f in demo.FAQ],
    ):
        assert len(names) == len(set(names))

    for service in demo.SERVICES:
        assert service.category in category_keys
        assert service.specialists
        assert set(service.specialists) <= specialist_keys
        assert service.duration_minutes % 15 == 0
        assert service.buffer_minutes % 15 == 0
        assert service.description

    for specialist in demo.SPECIALISTS:
        assert specialist.hours
        assert all(day != 6 for day, _start, _end in specialist.hours)
        for bio in (specialist.bio_uk, specialist.bio_en):
            assert "найкращ" not in bio.lower()
            assert "best" not in bio.lower()

    service_en = {s.en: s for s in demo.SERVICES}
    for review in demo.REVIEWS:
        assert review.specialist in service_en[review.service_en].specialists
        assert 1 <= review.rating <= 5
        assert review.days_ago >= 3


def test_faq_leaves_deposit_and_cancellation_rules_to_the_instructions():
    """Decided 08.11.2026: those rules reach the assistant from the
    instructions and get_salon_info; a FAQ copy would go stale."""
    questions = " ".join(f.question.lower() for f in demo.FAQ)

    assert len(demo.FAQ) == 10
    assert "передоплат" not in questions
    assert "скасувати" not in questions
