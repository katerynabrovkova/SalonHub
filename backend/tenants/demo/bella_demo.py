"""
Demo content and reset for the BellaBeauty demo salon (slug "bella-demo"),
used by the seed_bella_demo and reset_bella_demo management commands
(docs/DECISIONS.md § "Stage 17 sub-step order", 17.8 "Demo salon commands").

The tables in this module are the source of truth for the demo content:
every seed run overwrites the seeded fields with them. The seed only creates
or updates; the reset only deletes customer data. Both act only on the salon
with slug SALON_SLUG, inside its tenant context, with an explicit salon
filter on every query, in one transaction.

bella-demo is a made-up salon. Its demo reviews look like real ones on the
public pages, so it must only ever be shown as a demo salon.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Model, Q, QuerySet

from accounts.models import Account, AccountRole, Customer
from booking.models import Appointment, AppointmentStatus, GuestAccessToken
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from notifications.models import Notification
from payments.models import Payment
from reviews.models import Review
from specialists.models import Specialist, SpecialistService, WorkingHours
from tenants.models import FAQItem, Salon

SALON_SLUG = "bella-demo"

# The test account kept by the reset, with its Customer (decided 08.11.2026).
KEPT_ACCOUNT_EMAIL = "item8-new@example.com"

# Obviously fake: the assistant gives the salon phone to customers.
PLACEHOLDER_PHONE = "+380 00 000 00 00"
ADDRESS = "м. Чернівці, бульвар Героїв Крут, 17"

# Demo review customers: reserved example.com addresses, which never receive
# mail, and names that mark them in admin.
DEMO_REVIEW_EMAIL = "demo-review-{n:02d}@example.com"
DEMO_REVIEW_NAME = "Демо-клієнтка {n:02d}"


class DemoDataError(Exception):
    """The demo salon is not in the state a command needs; nothing was changed."""


@dataclass(frozen=True)
class CategoryRow:
    key: str
    uk: str
    en: str


@dataclass(frozen=True)
class ServiceRow:
    category: str
    uk: str
    en: str
    description: str
    duration_minutes: int
    buffer_minutes: int
    price: str
    specialists: tuple[str, ...]
    # Earlier English names of a row that already exists in the live salon,
    # so the seed renames it in place instead of creating a second one.
    previous_en: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpecialistRow:
    key: str
    uk: str
    en: str
    bio_uk: str
    bio_en: str
    # (day_of_week, "HH:MM", "HH:MM"), Monday = 0. Used only for a
    # specialist who has no working hours yet.
    hours: tuple[tuple[int, str, str], ...]


@dataclass(frozen=True)
class FAQRow:
    question: str
    answer: str
    previous_questions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewRow:
    specialist: str
    service_en: str
    rating: int
    text: str
    # The visit is on the specialist's nearest working day at or before
    # this many days before the first seed run.
    days_ago: int


CATEGORIES: tuple[CategoryRow, ...] = (
    CategoryRow("manicure", "Манікюр", "Manicure"),
    CategoryRow("pedicure", "Педикюр", "Pedicure"),
    CategoryRow("brows", "Брови", "Brows"),
    CategoryRow("lashes", "Вії", "Lashes"),
    CategoryRow("sugaring", "Шугаринг", "Sugaring"),
    CategoryRow("laser", "Лазерна епіляція", "Laser hair removal"),
    CategoryRow("face", "Догляд за обличчям", "Face care"),
    CategoryRow("hair", "Стрижки та укладки", "Haircut"),
)

_NAILS = ("anna", "olena")

SERVICES: tuple[ServiceRow, ...] = (
    # Manicure
    ServiceRow(
        "manicure",
        "Манікюр з покриттям гель-лаком і укріпленням",
        "Manicure with gel polish and strengthening",
        "Укріплення нігтя гелем під покриття, щоб манікюр довше тримався.",
        90,
        15,
        "30.00",
        _NAILS,
        previous_en=("coating and reinforcement",),
    ),
    ServiceRow(
        "manicure",
        "Класичний манікюр без покриття",
        "Classic manicure without polish",
        "Обробка кутикули і форма нігтів, без покриття.",
        45,
        0,
        "15.00",
        _NAILS,
    ),
    ServiceRow(
        "manicure",
        "Манікюр з покриттям гель-лаком",
        "Manicure with gel polish",
        "Манікюр і стійке покриття гель-лаком, тримається 2–3 тижні.",
        75,
        15,
        "25.00",
        _NAILS,
    ),
    ServiceRow(
        "manicure",
        "Нарощування нігтів",
        "Nail extensions",
        "Нарощування гелем на формах, з покриттям.",
        150,
        15,
        "45.00",
        ("olena",),
    ),
    ServiceRow(
        "manicure",
        "Чоловічий манікюр",
        "Men's manicure",
        "Охайна форма і обробка кутикули, без покриття.",
        45,
        0,
        "18.00",
        ("anna",),
    ),
    ServiceRow(
        "manicure",
        "Зняття покриття",
        "Polish removal",
        "Зняття гель-лаку без нового покриття.",
        15,
        0,
        "6.00",
        _NAILS,
    ),
    # Pedicure
    ServiceRow(
        "pedicure",
        "Педикюр з покриттям гель-лаком",
        "Pedicure with gel polish",
        "Педикюр і стійке покриття гель-лаком.",
        90,
        15,
        "35.00",
        _NAILS,
        previous_en=("gel polish application",),
    ),
    ServiceRow(
        "pedicure",
        "Класичний педикюр",
        "Classic pedicure",
        "Обробка стоп і нігтів, без покриття.",
        60,
        15,
        "25.00",
        _NAILS,
    ),
    ServiceRow(
        "pedicure",
        "Апаратний педикюр",
        "Machine pedicure",
        "Делікатна апаратна обробка стоп, підходить для чутливої шкіри.",
        75,
        15,
        "30.00",
        ("anna",),
    ),
    ServiceRow(
        "pedicure",
        "Експрес-педикюр",
        "Express pedicure",
        "Обробка лише пальців і нігтів.",
        45,
        0,
        "18.00",
        _NAILS,
    ),
    # Brows
    ServiceRow(
        "brows",
        "Корекція брів",
        "Brow shaping",
        "Форма брів воском або пінцетом.",
        30,
        0,
        "12.00",
        ("mariia",),
    ),
    ServiceRow(
        "brows",
        "Корекція і фарбування брів",
        "Brow shaping and tinting",
        "Форма і фарбування фарбою або хною.",
        45,
        0,
        "18.00",
        ("mariia",),
    ),
    ServiceRow(
        "brows",
        "Ламінування брів",
        "Brow lamination",
        "Укладання волосків у потрібному напрямку на 4–6 тижнів.",
        60,
        15,
        "28.00",
        ("mariia",),
    ),
    # Lashes
    ServiceRow(
        "lashes",
        "Ламінування вій",
        "Lash lift",
        "Підкручування і фарбування власних вій.",
        60,
        15,
        "30.00",
        ("mariia",),
    ),
    ServiceRow(
        "lashes",
        "Нарощування вій, класика",
        "Classic lash extensions",
        "Одна штучна вія на кожну власну.",
        120,
        15,
        "45.00",
        ("mariia",),
    ),
    ServiceRow(
        "lashes",
        "Нарощування вій 2D",
        "2D lash extensions",
        "Дві тонкі вії на кожну власну, об'ємніший ефект.",
        150,
        15,
        "55.00",
        ("mariia",),
    ),
    ServiceRow(
        "lashes",
        "Корекція нарощених вій",
        "Lash extension refill",
        "Заміна вій, що відросли, через 2–3 тижні.",
        90,
        15,
        "32.00",
        ("mariia",),
    ),
    # Sugaring
    ServiceRow(
        "sugaring",
        "Шугаринг ніг повністю",
        "Full leg sugaring",
        "Видалення волосся цукровою пастою на всій поверхні ніг.",
        60,
        15,
        "30.00",
        ("iryna",),
    ),
    ServiceRow(
        "sugaring",
        "Шугаринг глибокого бікіні",
        "Deep bikini sugaring",
        "Делікатна процедура для зони бікіні.",
        45,
        15,
        "28.00",
        ("iryna",),
    ),
    ServiceRow(
        "sugaring",
        "Шугаринг пахв",
        "Underarm sugaring",
        "Швидка процедура для зони пахв.",
        15,
        0,
        "10.00",
        ("iryna",),
    ),
    ServiceRow(
        "sugaring",
        "Шугаринг рук",
        "Arm sugaring",
        "Руки повністю або до ліктя.",
        30,
        0,
        "15.00",
        ("iryna",),
    ),
    # Laser hair removal
    ServiceRow(
        "laser",
        "Лазерна епіляція пахв",
        "Laser hair removal, underarms",
        "Діодний лазер; для стійкого результату потрібен курс процедур.",
        15,
        15,
        "25.00",
        ("iryna",),
    ),
    ServiceRow(
        "laser",
        "Лазерна епіляція глибокого бікіні",
        "Laser hair removal, deep bikini",
        "Діодний лазер, курс процедур.",
        45,
        15,
        "50.00",
        ("iryna",),
    ),
    ServiceRow(
        "laser",
        "Лазерна епіляція ніг повністю",
        "Laser hair removal, full legs",
        "Діодний лазер, курс процедур.",
        75,
        15,
        "90.00",
        ("iryna",),
    ),
    # Face care
    ServiceRow(
        "face",
        "Комбінована чистка обличчя",
        "Combined facial cleansing",
        "Ультразвукова і мануальна чистка, маска за типом шкіри.",
        90,
        15,
        "40.00",
        ("sofiia",),
    ),
    ServiceRow(
        "face",
        "Пілінг обличчя",
        "Facial peel",
        "Оновлення шкіри, підбирається після огляду.",
        45,
        15,
        "35.00",
        ("sofiia",),
    ),
    ServiceRow(
        "face",
        "Зволожувальний догляд",
        "Hydrating facial",
        "Очищення, сироватка і зволожувальна маска.",
        60,
        15,
        "38.00",
        ("sofiia",),
    ),
    # Haircuts and styling
    ServiceRow(
        "hair",
        "Підрізання кінчиків",
        "Trimming the ends",
        "Освіження довжини без зміни форми.",
        30,
        0,
        "12.00",
        ("sofiia",),
        previous_en=("trim the ends",),
    ),
    ServiceRow(
        "hair",
        "Жіноча стрижка",
        "Women's haircut",
        "Стрижка з миттям і укладкою.",
        60,
        15,
        "30.00",
        ("sofiia",),
    ),
    ServiceRow(
        "hair",
        "Укладка",
        "Blow-dry styling",
        "Миття і укладка феном.",
        45,
        0,
        "22.00",
        ("sofiia",),
    ),
)


def _weekdays(
    days: range | tuple[int, ...], start: str, end: str
) -> tuple[tuple[int, str, str], ...]:
    return tuple((day, start, end) for day in days)


SPECIALISTS: tuple[SpecialistRow, ...] = (
    SpecialistRow(
        "anna",
        "Анна",
        "Anna",
        "Майстриня манікюру і педикюру понад 6 років. Любить охайні натуральні форми "
        "і делікатну роботу з кутикулою.",
        "A manicure and pedicure specialist with more than 6 years of experience. She "
        "loves neat, natural shapes and gentle cuticle work.",
        _weekdays(range(0, 5), "09:00", "18:00"),
    ),
    SpecialistRow(
        "olena",
        "Олена",
        "Olena",
        "Майстриня манікюру і педикюру, робить нарощування нігтів і складний дизайн.",
        "A manicure and pedicure specialist who also does nail extensions and detailed nail art.",
        _weekdays(range(1, 6), "11:00", "20:00"),
    ),
    SpecialistRow(
        "mariia",
        "Марія",
        "Mariia",
        "Бровистка і лешмейкерка, підбирає форму брів і ефект вій під риси обличчя.",
        "A brow and lash artist who matches the brow shape and lash effect to your features.",
        _weekdays((0, 2, 4), "10:00", "19:00") + ((5, "10:00", "15:00"),),
    ),
    SpecialistRow(
        "iryna",
        "Ірина",
        "Iryna",
        "Майстриня депіляції: шугаринг і лазерна епіляція діодним лазером.",
        "A hair removal specialist: sugaring and diode laser hair removal.",
        (
            (0, "12:00", "20:00"),
            (2, "09:00", "13:00"),
            (2, "15:00", "20:00"),
            (3, "12:00", "20:00"),
        ),
    ),
    SpecialistRow(
        "sofiia",
        "Софія",
        "Sofiia",
        "Косметологиня і перукарка: догляд за обличчям, стрижки та укладки.",
        "A cosmetologist and hairdresser: face care, haircuts and styling.",
        _weekdays((1, 3), "10:00", "18:00") + ((4, "12:00", "20:00"), (5, "09:00", "14:00")),
    ),
)

FAQ: tuple[FAQRow, ...] = (
    FAQRow(
        "Де можна припаркуватися?",
        "Безкоштовна парковка є прямо перед салоном.",
        previous_questions=("Де можна припаркуватись?",),
    ),
    FAQRow(
        "Чи є у вас що поїсти?",
        "Так, у нас є міні-сендвічі й круасани, а також кава та чай.",
        previous_questions=("Чи є у вас шось поїсти?",),
    ),
    FAQRow(
        "Чи можна прийти з маленькою дитиною?",
        "Так, для дітей від 3 років у нас є дитячий куточок.",
    ),
    FAQRow(
        "Чи можна прийти з чоловіком або подругою?",
        "Так, у нас є зона відпочинку з диванчиками, де Вас можуть зачекати.",
        previous_questions=("Можу я прийти з чоловіком чи подругою",),
    ),
    FAQRow(
        "Чи можна робити манікюр, якщо є грибок нігтів?",
        "Якщо у Вас є грибкова інфекція нігтів або шкіри, ми рекомендуємо відкласти "
        "процедуру на ураженій ділянці до одужання. Це необхідно для Вашої безпеки, а також "
        "безпеки інших клієнтів і майстрів. Радимо спочатку проконсультуватися з "
        "дерматологом. Після лікування будемо раді записати Вас на процедуру.",
        previous_questions=("Чи можна робити манікюр якщо є грибок",),
    ),
    FAQRow(
        "Як можна оплатити послуги?",
        "Передоплату Ви вносите онлайн карткою під час запису, а решту суми — у салоні "
        "готівкою або карткою.",
    ),
    FAQRow(
        "Як підготуватися до шугарингу?",
        "Волоски мають бути довжиною 3–5 мм. За добу до процедури не засмагайте, а в день "
        "процедури не використовуйте скраб і жирний крем.",
    ),
    FAQRow(
        "Як підготуватися до лазерної епіляції?",
        "За 2 тижні не засмагайте, за 4 тижні не робіть шугаринг чи віск. За добу до "
        "процедури поголіть зону.",
    ),
    FAQRow(
        "Чи можна робити лазерну епіляцію під час вагітності?",
        "Під час вагітності та годування груддю ми не робимо лазерну епіляцію. Порадьтеся "
        "з лікарем, які процедури Вам зараз підходять.",
    ),
    FAQRow(
        "Що робити, якщо в мене алергія?",
        "Обов'язково скажіть про це майстрині до процедури. Перед фарбуванням брів, "
        "ламінуванням або нарощуванням вій ми можемо зробити тест на чутливість.",
    ),
)

REVIEWS: tuple[ReviewRow, ...] = (
    # Anna: 6 reviews, average 4.83
    ReviewRow(
        "anna",
        "Manicure with gel polish",
        5,
        "Дуже акуратна робота, покриття тримається вже третій тиждень.",
        9,
    ),
    ReviewRow(
        "anna",
        "Classic manicure without polish",
        5,
        "Все швидко і охайно, кутикулу оброблено дуже делікатно.",
        16,
    ),
    ReviewRow(
        "anna",
        "Machine pedicure",
        5,
        "Приємна атмосфера і уважна майстриня, ноги як нові.",
        23,
    ),
    ReviewRow(
        "anna",
        "Pedicure with gel polish",
        5,
        "Охайно і без поспіху, ще й пояснили, як доглядати за стопами.",
        30,
    ),
    ReviewRow(
        "anna",
        "Manicure with gel polish and strengthening",
        4,
        "Гарний результат, але процедура тривала трохи довше, ніж я очікувала.",
        37,
    ),
    ReviewRow(
        "anna",
        "Express pedicure",
        5,
        "Зручно, що можна швидко освіжити педикюр між великими процедурами.",
        44,
    ),
    # Olena: 5 reviews, average 4.6
    ReviewRow(
        "olena",
        "Nail extensions",
        5,
        "Нарощування виглядає натурально, форму підібрали саме таку, як я хотіла.",
        11,
    ),
    ReviewRow(
        "olena",
        "Manicure with gel polish",
        4,
        "Гарно, але колір довелося підбирати довго, палітра дуже велика.",
        18,
    ),
    ReviewRow(
        "olena",
        "Classic pedicure",
        5,
        "Дуже уважна майстриня, все охайно і чисто.",
        25,
    ),
    ReviewRow(
        "olena",
        "Manicure with gel polish and strengthening",
        4,
        "Покриття гарне, але запис почався на кілька хвилин пізніше.",
        32,
    ),
    ReviewRow(
        "olena",
        "Pedicure with gel polish",
        5,
        "Приходжу вже не вперше і завжди задоволена.",
        39,
    ),
    # Mariia: 7 reviews, average 4.86
    ReviewRow(
        "mariia",
        "Brow lamination",
        5,
        "Брови тепер лежать рівно, зранку майже не потрібно їх укладати.",
        8,
    ),
    ReviewRow(
        "mariia",
        "Classic lash extensions",
        5,
        "Вії виглядають природно і ніде не колють.",
        15,
    ),
    ReviewRow(
        "mariia",
        "Brow shaping and tinting",
        5,
        "Форму підібрали під моє обличчя, колір дуже природний.",
        22,
    ),
    ReviewRow(
        "mariia",
        "Lash lift",
        5,
        "Ефект тримається вже місяць, дуже задоволена.",
        29,
    ),
    ReviewRow(
        "mariia",
        "2D lash extensions",
        5,
        "Об'єм саме такий, як я хотіла, без лялькового ефекту.",
        36,
    ),
    ReviewRow(
        "mariia",
        "Brow shaping",
        4,
        "Добре, але хотілося трохи тоншу форму, наступного разу скажу заздалегідь.",
        43,
    ),
    ReviewRow(
        "mariia",
        "Lash extension refill",
        5,
        "Корекцію зробили швидко і акуратно.",
        50,
    ),
    # Iryna: 4 reviews, average 4.5
    ReviewRow(
        "iryna",
        "Full leg sugaring",
        5,
        "Майже не боляче, шкіра гладенька і без подразнень.",
        10,
    ),
    ReviewRow(
        "iryna",
        "Laser hair removal, underarms",
        4,
        "Після третьої процедури результат уже помітний, курс продовжую.",
        24,
    ),
    ReviewRow(
        "iryna",
        "Deep bikini sugaring",
        4,
        "Делікатно і швидко, легке почервоніння пройшло за день.",
        38,
    ),
    ReviewRow(
        "iryna",
        "Laser hair removal, full legs",
        5,
        "Все пояснили про курс і догляд, процедура комфортна.",
        52,
    ),
    # Sofiia: no reviews, so the assistant's "no reviews yet" rule can be checked.
)

_REVIEW_PUBLISHED_AFTER = dt.timedelta(hours=5)


def salon_zone(salon: Salon) -> ZoneInfo:
    return ZoneInfo(salon.timezone)


def _get_demo_salon() -> Salon:
    salon = Salon.objects.filter(slug=SALON_SLUG).first()
    if salon is None:
        raise DemoDataError(f'No salon with slug "{SALON_SLUG}" exists.')
    return salon


def _find_one[M: Model](queryset: "QuerySet[M]", label: str) -> M | None:
    rows = list(queryset[:2])
    if len(rows) > 1:
        raise DemoDataError(f"More than one row matches {label}; fix it in admin first.")
    return rows[0] if rows else None


def _time(value: str) -> dt.time:
    return dt.time.fromisoformat(value)


# --- seed ---------------------------------------------------------------------


def seed_bella_demo(*, now: dt.datetime) -> dict[str, int]:
    """
    Creates or updates the demo salon's content from the tables above.
    Returns how many rows of each kind were created. Never deletes.
    """
    salon = _get_demo_salon()
    created = dict.fromkeys(
        (
            "categories",
            "services",
            "specialists",
            "specialist_services",
            "working_hours",
            "faq_items",
            "customers",
            "appointments",
            "reviews",
        ),
        0,
    )
    with tenant_context(salon.id), transaction.atomic():
        salon.phone = PLACEHOLDER_PHONE
        salon.address = ADDRESS
        salon.save(update_fields=["phone", "address", "updated_at"])

        categories = _seed_categories(salon, created)
        specialists = _seed_specialists(salon, created)
        services = _seed_services(salon, categories, specialists, created)
        _seed_faq(salon, created)
        _seed_reviews(salon, now, specialists, services, created)
    return created


def _seed_categories(salon: Salon, created: dict[str, int]) -> dict[str, ServiceCategory]:
    result = {}
    for ordering, row in enumerate(CATEGORIES, start=1):
        category = _find_one(
            ServiceCategory.objects.filter(salon=salon, name__en=row.en),
            f'category "{row.en}"',
        )
        if category is None:
            category = ServiceCategory(salon=salon)
            created["categories"] += 1
        category.name = {"uk": row.uk, "en": row.en}
        category.ordering = ordering
        category.is_active = True
        category.save()
        result[row.key] = category
    return result


def _seed_specialists(salon: Salon, created: dict[str, int]) -> dict[str, Specialist]:
    result = {}
    for row in SPECIALISTS:
        specialist = _find_one(
            Specialist.objects.filter(salon=salon, name__en=row.en),
            f'specialist "{row.en}"',
        )
        if specialist is None:
            specialist = Specialist(salon=salon)
            created["specialists"] += 1
        specialist.name = {"uk": row.uk, "en": row.en}
        specialist.bio = {"uk": row.bio_uk, "en": row.bio_en}
        specialist.is_active = True
        specialist.save()
        # Hours only for a specialist who has none: hours already there (the
        # live Anna's, or any set in admin) are neither replaced nor doubled.
        if not WorkingHours.objects.filter(salon=salon, specialist=specialist).exists():
            for day, start, end in row.hours:
                WorkingHours.objects.create(
                    salon=salon,
                    specialist=specialist,
                    day_of_week=day,
                    start_time=_time(start),
                    end_time=_time(end),
                )
                created["working_hours"] += 1
        result[row.key] = specialist
    return result


def _seed_services(
    salon: Salon,
    categories: dict[str, ServiceCategory],
    specialists: dict[str, Specialist],
    created: dict[str, int],
) -> dict[str, Service]:
    result = {}
    for ordering, row in enumerate(SERVICES, start=1):
        service = _find_one(
            Service.objects.filter(salon=salon, name__en__in=(row.en, *row.previous_en)),
            f'service "{row.en}"',
        )
        if service is None:
            service = Service(salon=salon)
            created["services"] += 1
        service.category = categories[row.category]
        service.name = {"uk": row.uk, "en": row.en}
        service.description = row.description
        service.duration_minutes = row.duration_minutes
        service.buffer_minutes = row.buffer_minutes
        service.price = Decimal(row.price)
        service.ordering = ordering
        service.is_active = True
        service.save()
        for key in row.specialists:
            _link, link_created = SpecialistService.objects.get_or_create(
                salon=salon, specialist=specialists[key], service=service
            )
            created["specialist_services"] += int(link_created)
        result[row.en] = service
    return result


def _seed_faq(salon: Salon, created: dict[str, int]) -> None:
    for ordering, row in enumerate(FAQ, start=1):
        item = _find_one(
            FAQItem.objects.filter(
                salon=salon, question__in=(row.question, *row.previous_questions)
            ),
            f'FAQ item "{row.question}"',
        )
        if item is None:
            item = FAQItem(salon=salon)
            created["faq_items"] += 1
        item.question = row.question
        item.answer = row.answer
        item.ordering = ordering
        item.is_active = True
        item.save()


def _visit_start(salon: Salon, row: SpecialistRow, now: dt.datetime, days_ago: int) -> dt.datetime:
    """The start of the specialist's first working range on their nearest
    working day at or before `days_ago` days before `now`, salon-local."""
    zone = salon_zone(salon)
    first_start: dict[int, str] = {}
    for weekday, start, _end in sorted(row.hours):
        first_start.setdefault(weekday, start)
    visit_day = now.astimezone(zone).date() - dt.timedelta(days=days_ago)
    while visit_day.weekday() not in first_start:
        visit_day -= dt.timedelta(days=1)
    return dt.datetime.combine(visit_day, _time(first_start[visit_day.weekday()]), tzinfo=zone)


def _seed_reviews(
    salon: Salon,
    now: dt.datetime,
    specialists: dict[str, Specialist],
    services: dict[str, Service],
    created: dict[str, int],
) -> None:
    specialist_rows = {row.key: row for row in SPECIALISTS}
    for n, row in enumerate(REVIEWS, start=1):
        email = DEMO_REVIEW_EMAIL.format(n=n)
        customer = Customer.objects.filter(salon=salon, email=email).first()
        if customer is None:
            customer = Customer(salon=salon, email=email)
            created["customers"] += 1
        customer.name = DEMO_REVIEW_NAME.format(n=n)
        customer.phone = PLACEHOLDER_PHONE
        customer.save()

        specialist = specialists[row.specialist]
        service = services[row.service_en]
        # The visit keeps the date it got on the first run.
        appointment = (
            Appointment.objects.filter(
                salon=salon,
                customer=customer,
                specialist=specialist,
                service=service,
                status=AppointmentStatus.COMPLETED,
            )
            .order_by("pk")
            .first()
        )
        if appointment is None:
            start = _visit_start(salon, specialist_rows[row.specialist], now, row.days_ago)
            end = start + dt.timedelta(minutes=service.duration_minutes)
            appointment = Appointment.objects.create(
                salon=salon,
                customer=customer,
                specialist=specialist,
                service=service,
                start_datetime=start,
                end_datetime=end,
                blocked_until=end + dt.timedelta(minutes=service.buffer_minutes),
                service_price_at_booking=service.price,
                deposit_percentage_at_booking=salon.deposit_percentage,
                hold_expires_at=start,
                status=AppointmentStatus.COMPLETED,
            )
            created["appointments"] += 1

        review = Review.objects.filter(salon=salon, appointment=appointment).first()
        if review is None:
            review = Review.objects.create(
                salon=salon,
                appointment=appointment,
                customer=customer,
                specialist=specialist,
                rating=row.rating,
                text=row.text,
            )
            # created_at is auto_now_add; a review posted a few hours after
            # the visit reads better than 22 reviews posted today.
            Review.objects.filter(salon=salon, pk=review.pk).update(
                created_at=appointment.end_datetime + _REVIEW_PUBLISHED_AFTER
            )
            created["reviews"] += 1
        else:
            review.rating = row.rating
            review.text = row.text
            review.save(update_fields=["rating", "text", "updated_at"])


# --- reset --------------------------------------------------------------------


def _reset_querysets(salon: Salon) -> list[tuple[str, QuerySet]]:
    """
    What the reset deletes, in an order that respects every PROTECT foreign
    key: reviews and payments before appointments, appointments before
    customers, client accounts before their customers. Guest tokens and
    notifications would cascade, but are deleted explicitly so they are
    counted.
    """
    kept = _find_one(
        Account.objects.filter(salon=salon, email=KEPT_ACCOUNT_EMAIL),
        f'account "{KEPT_ACCOUNT_EMAIL}"',
    )
    if kept is None or kept.role != AccountRole.CLIENT or kept.customer_id is None:
        raise DemoDataError(
            f'The kept test account "{KEPT_ACCOUNT_EMAIL}" must exist in {SALON_SLUG} as a '
            "client with a linked Customer; nothing was deleted."
        )
    kept_customer_ids = {kept.customer_id} | set(
        Account.objects.filter(salon=salon, customer__isnull=False)
        .exclude(role=AccountRole.CLIENT)
        .values_list("customer_id", flat=True)
    )
    customers = Customer.objects.filter(salon=salon).exclude(pk__in=kept_customer_ids)
    return [
        ("reviews", Review.objects.filter(salon=salon)),
        ("payments", Payment.objects.filter(salon=salon)),
        ("guest_tokens", GuestAccessToken.objects.filter(salon=salon)),
        (
            "notifications",
            Notification.objects.filter(salon=salon).filter(
                Q(appointment__isnull=False) | Q(customer__in=customers)
            ),
        ),
        ("appointments", Appointment.objects.filter(salon=salon)),
        (
            "accounts",
            Account.objects.filter(salon=salon, role=AccountRole.CLIENT).exclude(pk=kept.pk),
        ),
        ("customers", customers),
    ]


def plan_bella_demo_reset() -> dict[str, int]:
    """How many rows the reset would delete, without deleting anything."""
    salon = _get_demo_salon()
    with tenant_context(salon.id):
        return {label: queryset.count() for label, queryset in _reset_querysets(salon)}


def reset_bella_demo() -> dict[str, int]:
    """Deletes the demo salon's customer data; returns the rows deleted per kind."""
    salon = _get_demo_salon()
    deleted = {}
    with tenant_context(salon.id), transaction.atomic():
        for label, queryset in _reset_querysets(salon):
            _total, per_model = queryset.delete()
            deleted[label] = per_model.get(queryset.model._meta.label, 0)
    return deleted
