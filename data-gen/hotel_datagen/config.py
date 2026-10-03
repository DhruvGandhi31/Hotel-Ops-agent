"""Knobs for the synthetic dataset. Changing anything here changes the ground truth."""

from datetime import date
from decimal import Decimal

# Unit price differences at or below this are accepted (and injected as negative examples).
PRICE_TOLERANCE_PCT = Decimal("2.0")

ORDER_DATE_START = date(2026, 6, 1)
ORDER_DATE_END = date(2026, 9, 10)

# Reason codes that should make reconciliation return `flag`.
FLAG_CODES = (
    "price_variance",
    "short_delivery",
    "duplicate_invoice",
    "unknown_po",
    "missing_po",
    "gst_miscalculated",
)

# Labelled conditions that must NOT cause a flag on their own.
#   description_mismatch    line worded differently from the PO and no supplier code printed
#   partial_delivery        less was received than ordered, and the invoice bills what was received
#   price_within_tolerance  unit price differs from the PO by <= PRICE_TOLERANCE_PCT
#   exact_resend            byte-identical copy of another file; ingestion must be a no-op
CONDITION_CODES = (
    "description_mismatch",
    "partial_delivery",
    "price_within_tolerance",
    "exact_resend",
)

# Share of all invoice files carrying each label. Turned into exact counts per run
# (round(rate * n)), so the realised distribution is known, not sampled.
RATES = {
    "price_variance": 0.10,
    "short_delivery": 0.08,
    "duplicate_invoice": 0.06,
    "unknown_po": 0.04,
    "missing_po": 0.03,
    "gst_miscalculated": 0.07,
    "description_mismatch": 0.12,
    "partial_delivery": 0.06,
    "price_within_tolerance": 0.06,
    "exact_resend": 0.02,
}

# Extra POs (with receipts) that never get invoiced: realistic noise in master data.
UNINVOICED_PO_RATE = 0.10

HOTEL = {
    "name": "The Wattle Lane Hotel",
    "address": "120 Wattle Lane, Lyrebird Point NSW 2999",
    "account_prefix": "WLH",
}

RECEIVERS = ("A. Okafor", "M. Rossi", "S. Tran", "J. Patel", "K. Walsh", "L. Moreau")
