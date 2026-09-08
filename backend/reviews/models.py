from django.db import models
from django.db.models.functions import Length

from accounts.models import Customer
from booking.models import Appointment
from core.models import TenantScopedModel, TimeStamped
from specialists.models import Specialist

REVIEW_TEXT_MAX_LENGTH = 2000

# Register the `length` lookup so the CheckConstraint below can express the
# bound as Length("text") <= N at the database level, robust to multi-byte
# text (django.db.models.functions.Length). Registering on the base field
# class is the documented pattern.
models.TextField.register_lookup(Length)


class Review(TenantScopedModel, TimeStamped):
    """
    docs/ARCHITECTURE.md § 11. One review per appointment (OneToOne), tied to
    a specific completed visit rather than the customer-salon relationship in
    general. Immutable after posting; deletion isn't exposed to anyone. No
    moderation/hide mechanism (docs/DECISIONS.md § Stage 11).
    """

    appointment = models.OneToOneField(Appointment, on_delete=models.PROTECT, related_name="review")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="reviews")
    specialist = models.ForeignKey(Specialist, on_delete=models.PROTECT, related_name="reviews")
    rating = models.PositiveSmallIntegerField()
    text = models.TextField(blank=True)

    class Meta(TenantScopedModel.Meta):
        abstract = False
        constraints = [
            *TenantScopedModel.Meta.constraints,
            models.CheckConstraint(
                condition=models.Q(rating__gte=1) & models.Q(rating__lte=5),
                name="review_rating_between_1_and_5",
            ),
            models.CheckConstraint(
                condition=models.Q(("text__length__lte", REVIEW_TEXT_MAX_LENGTH)),
                name="review_text_max_length_2000",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.rating}* review for {self.appointment}"
