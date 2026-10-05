import random
from decimal import Decimal

import pytest

from hotel_datagen.abn import format_abn, generate_abn, is_valid_abn
from hotel_datagen.money import format_money, gst_cents, gst_tolerance_cents, line_total_cents, round_cents
from hotel_datagen.output import invoice_key


def test_abn_checksum_against_published_example():
    # The ATO's own ABN, as printed on its website.
    assert is_valid_abn("51 824 753 556")
    assert not is_valid_abn("51 824 753 557")
    assert not is_valid_abn("5182475355")
    assert not is_valid_abn("01824753556")


def test_generated_abns_are_valid_and_formatted():
    rng = random.Random(0)
    for _ in range(500):
        abn = generate_abn(rng)
        assert is_valid_abn(abn), abn
        assert format_abn(abn).replace(" ", "") == abn


@pytest.mark.parametrize(
    ("value", "expected"),
    [(Decimal("0.5"), 1), (Decimal("1.5"), 2), (Decimal("2.5"), 3), (Decimal("2.4999"), 2)],
)
def test_round_half_up_not_bankers(value, expected):
    assert round_cents(value) == expected


def test_line_and_gst_arithmetic():
    assert line_total_cents(Decimal("2.034"), 6990) == 14218  # 14217.66
    assert gst_cents(1300_67) == 130_07
    assert gst_cents(5) == 1  # 0.5c rounds up
    assert gst_tolerance_cents(0) == 0
    assert gst_tolerance_cents(1) == 1
    assert gst_tolerance_cents(4) == 2


def test_format_money():
    assert format_money(143074) == "$1,430.74"
    assert format_money(5, symbol=False) == "0.05"
    assert format_money(-1999) == "-$19.99"


def test_invoice_key_separates_fields():
    assert invoice_key("51824753556", "INV1", 23) != invoice_key("51824753556", "INV12", 3)
    assert invoice_key("51 824 753 556", " inv-1 ", 100) == invoice_key("51824753556", "INV-1", 100)


def test_invoice_key_known_vector():
    """Also asserted against Postgres's invoice_key_of() in CI (smoke job); keep both in step."""
    assert invoice_key("51 824 753 556", " inv-1 ", 100) == KNOWN_KEY


KNOWN_KEY = "5724b35020e9eb048d47c1583989c74ee868d18306f42c34a480d685608eb344"
