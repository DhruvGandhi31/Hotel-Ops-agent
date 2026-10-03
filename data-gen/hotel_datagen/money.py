"""Integer-cent arithmetic. No floats touch money anywhere in the generator."""

from decimal import ROUND_HALF_UP, Decimal

GST_RATE = Decimal("0.10")


def round_cents(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def line_total_cents(quantity: Decimal, unit_price_cents: int) -> int:
    return round_cents(quantity * unit_price_cents)


def gst_cents(amount_cents: int, rate: Decimal = GST_RATE) -> int:
    return round_cents(Decimal(amount_cents) * rate)


def gst_tolerance_cents(n_taxable_lines: int) -> int:
    """Largest legitimate gap between stated GST and 10% of the taxable subtotal.

    Suppliers that round GST per line can drift by up to half a cent per taxable line.
    """
    return (n_taxable_lines + 1) // 2


def format_money(cents: int, symbol: bool = True) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    body = f"{cents // 100:,}.{cents % 100:02d}"
    return f"{sign}${body}" if symbol else f"{sign}{body}"
