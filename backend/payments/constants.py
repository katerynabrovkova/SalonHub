from datetime import timedelta

# How long a Payment may sit in REFUND_PENDING before the Stage 8.G sweep
# flags it for human review. Fixed for v1, not salon-configurable — a
# platform constant, not a business lever (docs/DECISIONS.md § Stage 8
# decisions). The sweep only flags, never auto-retries: a double refund is
# irreversible, so a stuck row surfaces to a human rather than being acted
# on automatically.
STUCK_REFUND_THRESHOLD = timedelta(hours=72)
