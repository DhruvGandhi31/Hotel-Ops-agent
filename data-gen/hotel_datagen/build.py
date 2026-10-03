"""Builds the synthetic world and injects labelled discrepancies.

Pure data, no I/O. Every random choice goes through one seeded Random in a fixed order,
so (n, seed) fully determines the dataset.

Files are numbered in arrival order and a copy (duplicate or exact re-send) always arrives
after its original. Ground truth assumes ingestion in file order.
"""

import copy
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from . import config
from .catalog import make_suppliers
from .model import (
    Dataset,
    InvoiceCase,
    InvoiceDocument,
    InvoiceLine,
    Item,
    LineTruth,
    POLine,
    PurchaseOrder,
    Receipt,
    ReceiptLine,
    Supplier,
)
from .money import gst_cents, gst_tolerance_cents, line_total_cents, round_cents

LINE_LEVEL_CODES = (
    "price_variance",
    "short_delivery",
    "description_mismatch",
    "partial_delivery",
    "price_within_tolerance",
)
DUPLICATE_BANNERS = ("COPY", "REMINDER - PAYMENT OVERDUE", "COPY INVOICE", "RE-ISSUED")


@dataclass
class _Plan:
    idx: int
    supplier: Supplier
    codes: list[str] = field(default_factory=list)
    order_date: date | None = None
    po_number: str | None = None


def build_dataset(n: int, seed: int) -> Dataset:
    rng = random.Random(seed)
    suppliers = make_suppliers(rng)
    accounts = {s.key: f"{config.HOTEL['account_prefix']}{rng.randint(100, 999)}" for s in suppliers}

    quotas = {code: round(rate * n) for code, rate in config.RATES.items()}
    n_base = n - quotas["duplicate_invoice"] - quotas["exact_resend"]
    if n_base < 1:
        raise ValueError(f"n={n} is too small for the configured rates")

    plans = [_Plan(i, rng.choice(suppliers)) for i in range(n_base)]
    _assign_codes(plans, quotas, rng)
    extra = [_Plan(n_base + i, rng.choice(suppliers)) for i in range(round(config.UNINVOICED_PO_RATE * n_base))]
    for p in plans + extra:
        p.order_date = _random_date(rng)

    # PO numbers follow order date, like a real purchasing system.
    with_po = sorted((p for p in plans + extra if "unknown_po" not in p.codes), key=lambda p: (p.order_date, p.idx))
    for seq, p in enumerate(with_po, start=4501):
        p.po_number = f"PO-{seq:06d}"
    known_pos = {p.po_number for p in with_po}

    pos: list[PurchaseOrder] = []
    receipts: list[Receipt] = []
    cases: list[InvoiceCase] = []
    for grn_seq, p in enumerate(plans + extra, start=80001):
        po, receipt, case = _build_case(p, rng, known_pos, accounts[p.supplier.key], f"GRN-{grn_seq:06d}")
        if po:
            pos.append(po)
            receipts.append(receipt)
        if p.idx < n_base:
            cases.append(case)

    _assign_invoice_numbers(cases, suppliers, rng)
    cases += _make_copies(cases, quotas, rng)
    _assign_file_ids(cases, rng)

    pos.sort(key=lambda po: po.po_number)
    receipts.sort(key=lambda r: r.receipt_number)
    return Dataset(
        seed=seed, n=n, suppliers=suppliers, purchase_orders=pos, receipts=receipts, cases=cases, quotas=quotas
    )


def _assign_codes(plans: list[_Plan], quotas: dict[str, int], rng: random.Random) -> None:
    order = list(range(len(plans)))
    rng.shuffle(order)
    k_unknown, k_missing = quotas["unknown_po"], quotas["missing_po"]
    for i in order[:k_unknown]:
        plans[i].codes.append("unknown_po")
    for i in order[k_unknown : k_unknown + k_missing]:
        plans[i].codes.append("missing_po")

    # Without a usable PO number the line-level checks can't run, so keep them apart.
    matchable = sorted(order[k_unknown + k_missing :])
    for code in LINE_LEVEL_CODES:
        if quotas[code] > len(matchable):
            raise ValueError(f"not enough invoices for {quotas[code]} x {code}")
        for i in sorted(rng.sample(matchable, quotas[code])):
            plans[i].codes.append(code)
    # Half the GST errors go to suppliers that sell GST-free goods, so the "GST charged on a
    # GST-free item" variant is well represented (all-taxable suppliers can't produce it).
    k = quotas["gst_miscalculated"]
    has_free = [i for i, p in enumerate(plans) if any(item.gst_free for item in p.supplier.items)]
    gst_pick = rng.sample(has_free, min(len(has_free), (k + 1) // 2))
    taken = set(gst_pick)
    gst_pick += rng.sample([i for i in range(len(plans)) if i not in taken], k - len(gst_pick))
    for i in sorted(gst_pick):
        plans[i].codes.append("gst_miscalculated")


def _build_case(
    plan: _Plan, rng: random.Random, known_pos: set[str], account: str, receipt_number: str
) -> tuple[PurchaseOrder | None, Receipt | None, InvoiceCase]:
    s, codes = plan.supplier, plan.codes

    # --- lines ordered -------------------------------------------------------------
    min_lines = 1
    if "short_delivery" in codes and "partial_delivery" in codes:
        min_lines = 2
    if "price_variance" in codes and "price_within_tolerance" in codes:
        min_lines = 2
    n_lines = _line_count(rng, len(s.items), min_lines)
    items = rng.sample(s.items, n_lines)
    if "price_within_tolerance" in codes and not any(i.price_cents >= 500 for i in items):
        items[0] = rng.choice([i for i in s.items if i.price_cents >= 500 and i not in items])
    qtys = [_quantity(item, s, rng) for item in items]
    idxs = list(range(n_lines))

    # --- which lines carry which injected condition --------------------------------
    reserve = 1 if "partial_delivery" in codes else 0  # partial delivery needs a line of its own
    short = rng.sample(idxs, min(n_lines - reserve, rng.randint(1, 2))) if "short_delivery" in codes else []
    partial = [rng.choice([i for i in idxs if i not in short])] if "partial_delivery" in codes else []
    tolerance = []
    if "price_within_tolerance" in codes:
        tolerance = [rng.choice([i for i in idxs if items[i].price_cents >= 500])]
    variance = []
    if "price_variance" in codes:
        pool = [i for i in idxs if i not in tolerance]
        variance = rng.sample(pool, min(len(pool), rng.randint(1, 2)))
    mismatch = rng.sample(idxs, min(n_lines, rng.randint(1, 3))) if "description_mismatch" in codes else []

    for i in short + partial:
        if items[i].unit != "kg" and qtys[i] < 2:
            qtys[i] = Decimal(2)

    received = list(qtys)
    for i in short + partial:
        received[i] = _reduce(qtys[i], items[i], s.qty_decimals, rng)
    invoiced_qty = list(qtys)
    for i in partial:
        invoiced_qty[i] = received[i]  # billed for what arrived: fine

    prices = [item.price_cents for item in items]
    for i in variance:
        prices[i] = _bump_price(items[i].price_cents, rng)
    for i in tolerance:
        prices[i] = _nudge_price(items[i].price_cents, rng)

    # --- dates ---------------------------------------------------------------------
    expected = plan.order_date + timedelta(days=rng.randint(1, 4))
    received_date = expected + timedelta(days=rng.choice((0, 0, 0, 1)))
    invoice_date = received_date + timedelta(days=rng.randint(0, 3))
    due_date = invoice_date + timedelta(days=s.payment_terms_days)

    # --- master data ---------------------------------------------------------------
    po = receipt = None
    if plan.po_number:
        po = PurchaseOrder(
            po_number=plan.po_number,
            supplier_key=s.key,
            order_date=plan.order_date,
            expected_date=expected,
            lines=[
                POLine(i + 1, item.code, item.description, item.unit, qtys[i], item.price_cents, not item.gst_free)
                for i, item in enumerate(items)
            ],
        )
        receipt = Receipt(
            receipt_number=receipt_number,
            po_number=plan.po_number,
            received_date=received_date,
            received_by=rng.choice(config.RECEIVERS),
            lines=[ReceiptLine(i + 1, received[i]) for i in idxs],
        )

    # --- invoice lines and GST -----------------------------------------------------
    totals = [line_total_cents(invoiced_qty[i], prices[i]) for i in idxs]
    master_gst = [not item.gst_free for item in items]
    method = "line" if s.template == "D" else "invoice"
    correct, per_line = _compute_gst(totals, master_gst, method)
    taxable_subtotal = sum(t for t, g in zip(totals, master_gst, strict=True) if g)
    tolerance_cents = gst_tolerance_cents(sum(master_gst))
    doc_gst_flags = list(master_gst)
    stated, variant, gst_lines = correct, None, []
    if "gst_miscalculated" in codes:
        stated, per_line, doc_gst_flags, variant, gst_lines = _inject_gst_error(
            totals, master_gst, method, correct, tolerance_cents, rng
        )

    lines = []
    for i, item in enumerate(items):
        described_differently = i in mismatch
        lines.append(
            InvoiceLine(
                line_no=i + 1,
                supplier_code=None if described_differently or not s.prints_codes else item.code,
                description=rng.choice(item.aliases) if described_differently else item.description,
                quantity=invoiced_qty[i],
                unit=item.unit,
                unit_price_cents=prices[i],
                gst_applicable=doc_gst_flags[i],
                line_total_cents=totals[i],
                gst_cents=per_line[i],
            )
        )

    if "unknown_po" in codes:
        printed_po = _style_po(_fake_po(rng, known_pos), s.po_style)
    elif "missing_po" in codes:
        printed_po = None
    else:
        printed_po = _style_po(plan.po_number, s.po_style)

    subtotal = sum(totals)
    doc = InvoiceDocument(
        supplier_key=s.key,
        invoice_number="",  # assigned later, in invoice-date order per supplier
        invoice_date=invoice_date,
        due_date=due_date,
        po_number=printed_po,
        lines=lines,
        subtotal_cents=subtotal,
        gst_cents=stated,
        total_cents=subtotal + stated,
        gst_method=method,
        account_number=account,
        docket_number=f"DD{rng.randint(100000, 999999)}",
    )

    line_truth = []
    for i in idxs:
        issues = [
            code
            for code, chosen in (
                ("short_delivery", short),
                ("partial_delivery", partial),
                ("price_variance", variance),
                ("price_within_tolerance", tolerance),
                ("description_mismatch", mismatch),
                ("gst_miscalculated", gst_lines),
            )
            if i in chosen
        ]
        line_truth.append(
            LineTruth(
                line_no=i + 1,
                po_line_no=i + 1 if po else None,
                issues=issues,
                po_quantity=qtys[i] if po else None,
                received_quantity=received[i] if po else None,
                po_unit_price_cents=items[i].price_cents if po else None,
                master_gst_applicable=master_gst[i],
            )
        )

    case = InvoiceCase(
        case_id=plan.idx,
        doc=doc,
        reason_codes=[c for c in config.FLAG_CODES if c in codes],
        conditions=[c for c in config.CONDITION_CODES if c in codes],
        line_truth=line_truth,
        gst_truth={
            "method": method,
            "taxable_subtotal_cents": taxable_subtotal,
            "correct_cents": correct,
            "stated_cents": stated,
            "tolerance_cents": tolerance_cents,
            "variant": variant,
        },
        true_po_number=plan.po_number,
    )
    return po, receipt, case


def _compute_gst(totals: list[int], flags: list[bool], method: str, rate: Decimal | None = None) -> tuple[int, list]:
    kwargs = {"rate": rate} if rate is not None else {}
    if method == "line":
        per_line = [gst_cents(t, **kwargs) if f else 0 for t, f in zip(totals, flags, strict=True)]
        return sum(per_line), per_line
    taxable = sum(t for t, f in zip(totals, flags, strict=True) if f)
    return gst_cents(taxable, **kwargs), [None] * len(totals)


def _inject_gst_error(
    totals: list[int], master: list[bool], method: str, correct: int, tolerance: int, rng: random.Random
) -> tuple[int, list, list[bool], str, list[int]]:
    # Must stay clearly wrong even when compared against invoice-level 10% (which can sit
    # `tolerance` cents away from a correct per-line figure).
    threshold = 2 * tolerance + 2
    flags = list(master)
    idxs = range(len(totals))

    free_big = [i for i in idxs if not master[i] and gst_cents(totals[i]) > threshold]
    wrong_rates = [
        r
        for r in (Decimal("0.125"), Decimal("0.15"))
        if abs(_compute_gst(totals, master, method, r)[0] - correct) > threshold
    ]
    variants = ["arithmetic_error"]
    if free_big:
        # Most common real-world error, and the one that proves the reconciler uses master data
        # rather than the GST flags printed on the invoice, so weight it up.
        variants += ["gst_on_gst_free"] * 3
    if wrong_rates:
        variants.append("wrong_rate")
    variant = rng.choice(variants)

    affected: list[int] = []
    if variant == "gst_on_gst_free":
        i = rng.choice(free_big)
        flags[i] = True
        affected = [i]
        stated, per_line = _compute_gst(totals, flags, method)
    elif variant == "wrong_rate":
        stated, per_line = _compute_gst(totals, master, method, rng.choice(wrong_rates))
        affected = [i for i in idxs if master[i]]
    else:
        offset = max(threshold + 1, 50, round_cents(Decimal(correct) * Decimal(rng.randint(10, 30)) / 100))
        if rng.random() < 0.5 and correct - offset >= 0:
            offset = -offset
        stated, per_line = _compute_gst(totals, master, method)
        taxable = [i for i in idxs if master[i]]
        if method == "line" and taxable and per_line[taxable[0]] + offset >= 0:
            j = rng.choice([i for i in taxable if per_line[i] + offset >= 0])
            per_line[j] += offset
            affected = [j]
        stated += offset

    assert abs(stated - correct) > threshold
    return stated, per_line, flags, variant, affected


def _assign_invoice_numbers(cases: list[InvoiceCase], suppliers: list[Supplier], rng: random.Random) -> None:
    by_key = {s.key: s for s in suppliers}
    counters = {s.key: rng.randint(1000, 40000) for s in suppliers}
    for case in sorted(cases, key=lambda c: (c.doc.invoice_date, c.case_id)):
        s = by_key[case.doc.supplier_key]
        counters[s.key] += rng.randint(1, 25)  # suppliers invoice other customers in between
        case.doc.invoice_number = s.invoice_number_format.format(counters[s.key])


def _make_copies(cases: list[InvoiceCase], quotas: dict[str, int], rng: random.Random) -> list[InvoiceCase]:
    clean = [c for c in cases if not c.reason_codes]
    if len(clean) < quotas["duplicate_invoice"]:
        raise ValueError("not enough clean invoices to duplicate")
    dup_sources = rng.sample(clean, quotas["duplicate_invoice"])
    dup_ids = {c.case_id for c in dup_sources}
    resend_sources = rng.sample([c for c in cases if c.case_id not in dup_ids], quotas["exact_resend"])

    copies = []
    next_id = len(cases)
    for src in dup_sources:
        # Half are visibly marked, half differ only in PDF metadata (same text, different bytes).
        banner = rng.choice(DUPLICATE_BANNERS) if rng.random() < 0.5 else None
        resent_on = src.doc.invoice_date + timedelta(days=rng.randint(14, 40))
        copies.append(
            InvoiceCase(
                case_id=next_id,
                doc=copy.deepcopy(src.doc),
                reason_codes=["duplicate_invoice"],
                conditions=list(src.conditions),
                line_truth=copy.deepcopy(src.line_truth),
                gst_truth=dict(src.gst_truth),
                true_po_number=src.true_po_number,
                duplicate_of=src.case_id,
                banner=banner,
                pdf_subject=f"Tax invoice (resent {resent_on.isoformat()})",
            )
        )
        next_id += 1
    for src in resend_sources:
        copies.append(
            InvoiceCase(
                case_id=next_id,
                doc=copy.deepcopy(src.doc),
                reason_codes=list(src.reason_codes),
                conditions=[*src.conditions, "exact_resend"],
                line_truth=copy.deepcopy(src.line_truth),
                gst_truth=dict(src.gst_truth),
                true_po_number=src.true_po_number,
                exact_resend_of=src.case_id,
                banner=src.banner,
                pdf_subject=src.pdf_subject,
            )
        )
        next_id += 1
    return copies


def _assign_file_ids(cases: list[InvoiceCase], rng: random.Random) -> None:
    order = list(cases)
    rng.shuffle(order)
    pos = {c.case_id: i for i, c in enumerate(order)}
    for c in order[:]:
        src = c.duplicate_of if c.duplicate_of is not None else c.exact_resend_of
        if src is not None and pos[src] > pos[c.case_id]:
            a, b = pos[src], pos[c.case_id]
            order[a], order[b] = order[b], order[a]
            pos[order[a].case_id], pos[order[b].case_id] = a, b
    for i, c in enumerate(order, start=1):
        c.file_id = f"inv_{i:04d}"
    cases.sort(key=lambda c: c.file_id)


def _random_date(rng: random.Random) -> date:
    span = (config.ORDER_DATE_END - config.ORDER_DATE_START).days
    return config.ORDER_DATE_START + timedelta(days=rng.randint(0, span))


def _line_count(rng: random.Random, n_items: int, min_lines: int) -> int:
    n = rng.choice((1, 2, 2, 3, 3, 3, 4, 4, 5, 5, 6, 7, 8, 10))
    return max(min_lines, min(n, n_items))


def _quantity(item: Item, s: Supplier, rng: random.Random) -> Decimal:
    lo, hi = item.qty_range
    if item.unit == "kg":
        dp = s.qty_decimals
        return Decimal(rng.randint(lo * 10**dp, hi * 10**dp)).scaleb(-dp)
    return Decimal(rng.randint(lo, hi))


def _reduce(q: Decimal, item: Item, dp: int, rng: random.Random) -> Decimal:
    """A received quantity strictly between 0 and q."""
    if item.unit == "kg":
        step = Decimal(1).scaleb(-dp)
        reduced = (q * Decimal(rng.randint(40, 85)) / 100).quantize(step)
        return min(max(reduced, step), q - step)
    hi = int(q) - 1
    lo = min(max(1, int(q) * 4 // 10), hi)
    return Decimal(rng.randint(lo, hi))


def _bump_price(po_price: int, rng: random.Random) -> int:
    """An overcharge comfortably beyond the tolerance (5-25%)."""
    new = round_cents(po_price * (1 + Decimal(rng.randint(500, 2500)) / 10000))
    while (new - po_price) * 100 <= (config.PRICE_TOLERANCE_PCT + 1) * po_price:
        new += 1
    return new


def _nudge_price(po_price: int, rng: random.Random) -> int:
    """A change of at most half the tolerance, either direction. Needs po_price >= 500."""
    # <= 0.9% so that rounding (at most +0.5c on a >= 500c price) stays within 1%.
    pct = Decimal(rng.randint(30, 90)) / 10000 * rng.choice((1, -1))
    new = round_cents(po_price * (1 + pct))
    if new == po_price:
        new += 1
    assert abs(new - po_price) * 100 <= config.PRICE_TOLERANCE_PCT * po_price / 2
    return new


def _fake_po(rng: random.Random, known: set[str]) -> str:
    """A PO number that does not exist: either a digit transposition of a real one or made up."""
    real = sorted(known)
    while True:
        if real and rng.random() < 0.5:
            digits = list(rng.choice(real)[3:])
            j = rng.randrange(len(digits) - 1)
            digits[j], digits[j + 1] = digits[j + 1], digits[j]
            candidate = "PO-" + "".join(digits)
        else:
            candidate = f"PO-{rng.randint(1000, 4400):06d}"
        if candidate not in known:
            return candidate


def _style_po(po_number: str, style: str) -> str:
    return po_number.replace("-", "") if style == "compact" else po_number
