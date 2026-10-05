"""Writes a built Dataset to disk: PDFs, ground truth, master data, seed SQL and a report."""

import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from . import GENERATOR_VERSION, config
from .model import Dataset, InvoiceCase, Supplier
from .render import prints_unit, render_invoice


def invoice_key(supplier_abn: str, invoice_number: str, total_cents: int) -> str:
    """Idempotency key. Fields are '|'-joined so ('INV1', 23) and ('INV12', 3) can't collide."""
    raw = f"{supplier_abn.replace(' ', '')}|{invoice_number.strip().upper()}|{total_cents}"
    return hashlib.sha256(raw.encode()).hexdigest()


def write_dataset(ds: Dataset, out: Path) -> dict:
    inv_dir, gt_dir = out / "invoices", out / "ground_truth"
    for d in (inv_dir, gt_dir):
        d.mkdir(parents=True, exist_ok=True)
        for stale in [*d.glob("*.pdf"), *d.glob("*.json")]:
            stale.unlink()

    suppliers = {s.key: s for s in ds.suppliers}
    by_case = {c.case_id: c for c in ds.cases}
    pdfs: dict[int, bytes] = {}
    # Originals first so exact re-sends can reuse their bytes.
    for case in sorted(ds.cases, key=lambda c: c.exact_resend_of is not None):
        if case.exact_resend_of is not None:
            pdfs[case.case_id] = pdfs[case.exact_resend_of]
        else:
            pdfs[case.case_id] = render_invoice(case, suppliers[case.doc.supplier_key])

    manifest_files = []
    for case in ds.cases:
        pdf = pdfs[case.case_id]
        (inv_dir / f"{case.file_id}.pdf").write_bytes(pdf)
        gt = ground_truth(case, suppliers[case.doc.supplier_key], by_case, hashlib.sha256(pdf).hexdigest())
        _write_json(gt_dir / f"{case.file_id}.json", gt)
        manifest_files.append(
            {
                "file_id": case.file_id,
                "file": gt["file"],
                "file_sha256": gt["file_sha256"],
                "invoice_key": gt["invoice_key"],
                "template": gt["template"],
                "reason_codes": gt["labels"]["reason_codes"],
                "conditions": gt["labels"]["conditions"],
                "expected_status": gt["labels"]["expected_status"],
                "expected_ingestion": gt["labels"]["expected_ingestion"],
            }
        )

    manifest = {
        "generator_version": GENERATOR_VERSION,
        "seed": ds.seed,
        "n": ds.n,
        "price_tolerance_pct": str(config.PRICE_TOLERANCE_PCT),
        "quotas": ds.quotas,
        "files": manifest_files,
    }
    _write_json(out / "manifest.json", manifest)
    _write_json(out / "master_data.json", master_data(ds))
    (out / "seed.sql").write_text(seed_sql(ds), encoding="utf-8", newline="\n")
    report = render_report(ds)
    (out / "REPORT.md").write_text(report, encoding="utf-8", newline="\n")
    return manifest


def ground_truth(case: InvoiceCase, s: Supplier, by_case: dict[int, InvoiceCase], sha256: str) -> dict:
    d = case.doc

    def file_of(case_id: int | None) -> str | None:
        return None if case_id is None else by_case[case_id].file_id

    if case.exact_resend_of is not None:
        expected_status, ingestion = None, "noop"
    else:
        expected_status = "flag" if case.reason_codes else "recommend_approve"
        ingestion = "insert"
    return {
        "file": f"invoices/{case.file_id}.pdf",
        "file_sha256": sha256,
        "template": s.template,
        "invoice_key": invoice_key(s.abn, d.invoice_number, d.total_cents),
        # Exactly what is printed: the extraction target.
        "document": {
            "supplier_name": s.name,
            "supplier_abn": s.abn,
            "supplier_address": s.address,
            "supplier_email": s.email,
            "supplier_phone": s.phone,
            "bill_to_name": config.HOTEL["name"],
            "invoice_number": d.invoice_number,
            "invoice_date": d.invoice_date.isoformat(),
            "due_date": d.due_date.isoformat(),
            "po_number": d.po_number,
            "currency": "AUD",
            "lines": [
                {
                    "line_no": ln.line_no,
                    "supplier_code": ln.supplier_code,
                    "description": ln.description,
                    "quantity": _num(ln.quantity),
                    "unit": ln.unit if prints_unit(s.template, ln.unit) else None,
                    "unit_price_cents": ln.unit_price_cents,
                    "gst_applicable": ln.gst_applicable,
                    "line_total_cents": ln.line_total_cents,
                    "gst_cents": ln.gst_cents,
                }
                for ln in d.lines
            ],
            "subtotal_cents": d.subtotal_cents,
            "gst_cents": d.gst_cents,
            "total_cents": d.total_cents,
        },
        # What reconciliation should conclude.
        "labels": {
            "reason_codes": case.reason_codes,
            "conditions": case.conditions,
            "expected_status": expected_status,
            "expected_ingestion": ingestion,
            "true_po_number": case.true_po_number,
            "duplicate_of": file_of(case.duplicate_of),
            "exact_resend_of": file_of(case.exact_resend_of),
            "gst": case.gst_truth,
            "lines": [
                {
                    "line_no": t.line_no,
                    "po_line_no": t.po_line_no,
                    "issues": t.issues,
                    "po_quantity": _num(t.po_quantity),
                    "received_quantity": _num(t.received_quantity),
                    "po_unit_price_cents": t.po_unit_price_cents,
                    "master_gst_applicable": t.master_gst_applicable,
                }
                for t in case.line_truth
            ],
        },
    }


def master_data(ds: Dataset) -> dict:
    return {
        "suppliers": [
            {
                "key": s.key,
                "abn": s.abn,
                "name": s.name,
                "category": s.category,
                "email": s.email,
                "phone": s.phone,
                "address": s.address,
                "payment_terms_days": s.payment_terms_days,
            }
            for s in ds.suppliers
        ],
        "purchase_orders": [
            {
                "po_number": po.po_number,
                "supplier_key": po.supplier_key,
                "order_date": po.order_date.isoformat(),
                "expected_date": po.expected_date.isoformat(),
                "lines": [
                    {
                        "line_no": ln.line_no,
                        "supplier_sku": ln.supplier_sku,
                        "description": ln.description,
                        "unit": ln.unit,
                        "quantity": _num(ln.quantity),
                        "unit_price_cents": ln.unit_price_cents,
                        "gst_applicable": ln.gst_applicable,
                    }
                    for ln in po.lines
                ],
            }
            for po in ds.purchase_orders
        ],
        "receipts": [
            {
                "receipt_number": r.receipt_number,
                "po_number": r.po_number,
                "received_date": r.received_date.isoformat(),
                "received_by": r.received_by,
                "lines": [
                    {"po_line_no": ln.po_line_no, "quantity_received": _num(ln.quantity_received)} for ln in r.lines
                ],
            }
            for r in ds.receipts
        ],
    }


def seed_sql(ds: Dataset) -> str:
    """Master data only (suppliers, POs, receipts). Invoices arrive through ingestion.

    Idempotent (ON CONFLICT DO NOTHING). Refuses to load on top of master data from a
    different dataset: with DO NOTHING, same-numbered POs would silently keep stale lines.
    """
    abn_of = {s.key: s.abn for s in ds.suppliers}
    fingerprint = hashlib.sha256(json.dumps([GENERATOR_VERSION, master_data(ds)], sort_keys=True).encode()).hexdigest()
    out = [
        f"-- Generated by data-gen v{GENERATOR_VERSION}, seed={ds.seed}, n={ds.n}. Do not edit.",
        "DO $$",
        "DECLARE existing text;",
        "BEGIN",
        "  SELECT value INTO existing FROM seed_metadata WHERE key = 'dataset_fingerprint';",
        f"  IF existing IS DISTINCT FROM {_lit(fingerprint)}",
        "     AND (existing IS NOT NULL OR EXISTS (SELECT 1 FROM suppliers)) THEN",
        "    RAISE EXCEPTION 'ops already holds master data from a different dataset (seed or generator "
        "version); reset with docker compose down -v';",
        "  END IF;",
        "END $$;",
        "INSERT INTO seed_metadata (key, value) VALUES "
        f"('dataset_fingerprint', {_lit(fingerprint)}) ON CONFLICT (key) DO NOTHING;",
        "",
    ]
    for s in ds.suppliers:
        out.append(
            "INSERT INTO suppliers (abn, name, category, email, phone, address, payment_terms_days) VALUES "
            f"({_lit(s.abn)}, {_lit(s.name)}, {_lit(s.category)}, {_lit(s.email)}, {_lit(s.phone)}, "
            f"{_lit(s.address)}, {s.payment_terms_days}) ON CONFLICT (abn) DO NOTHING;"
        )
    out.append("")
    for po in ds.purchase_orders:
        out.append(
            "INSERT INTO purchase_orders (po_number, supplier_id, order_date, expected_delivery_date) "
            f"SELECT {_lit(po.po_number)}, id, {_lit(po.order_date.isoformat())}, {_lit(po.expected_date.isoformat())} "
            f"FROM suppliers WHERE abn = {_lit(abn_of[po.supplier_key])} ON CONFLICT (po_number) DO NOTHING;"
        )
        for ln in po.lines:
            out.append(
                "INSERT INTO purchase_order_lines (purchase_order_id, line_no, supplier_sku, description, unit, "
                "quantity, unit_price_cents, gst_applicable) "
                f"SELECT id, {ln.line_no}, {_lit(ln.supplier_sku)}, {_lit(ln.description)}, {_lit(ln.unit)}, "
                f"{ln.quantity}, {ln.unit_price_cents}, {str(ln.gst_applicable).lower()} "
                f"FROM purchase_orders WHERE po_number = {_lit(po.po_number)} "
                "ON CONFLICT (purchase_order_id, line_no) DO NOTHING;"
            )
    out.append("")
    for r in ds.receipts:
        out.append(
            "INSERT INTO receipts (receipt_number, purchase_order_id, received_date, received_by) "
            f"SELECT {_lit(r.receipt_number)}, id, {_lit(r.received_date.isoformat())}, {_lit(r.received_by)} "
            f"FROM purchase_orders WHERE po_number = {_lit(r.po_number)} ON CONFLICT (receipt_number) DO NOTHING;"
        )
        for ln in r.lines:
            out.append(
                "INSERT INTO receipt_lines (receipt_id, purchase_order_line_id, quantity_received) "
                f"SELECT r.id, pol.id, {ln.quantity_received} FROM receipts r "
                "JOIN purchase_order_lines pol ON pol.purchase_order_id = r.purchase_order_id "
                f"AND pol.line_no = {ln.po_line_no} WHERE r.receipt_number = {_lit(r.receipt_number)} "
                "ON CONFLICT (receipt_id, purchase_order_line_id) DO NOTHING;"
            )
    return "\n".join(out) + "\n"


def render_report(ds: Dataset) -> str:
    n = len(ds.cases)
    originals = [c for c in ds.cases if c.duplicate_of is None and c.exact_resend_of is None]
    scored = [c for c in ds.cases if c.exact_resend_of is None]
    suppliers = {s.key: s for s in ds.suppliers}

    def table(codes: tuple[str, ...]) -> list[str]:
        rows = [
            "| Code | Target rate | Injected | Scored files carrying it | Share of scored files |",
            "|---|---:|---:|---:|---:|",
        ]
        for code in codes:
            if code == "exact_resend":
                rows.append(f"| `{code}` | {config.RATES[code]:.0%} | {ds.quotas[code]} | not scored | |")
                continue
            k = sum(code in c.reason_codes or code in c.conditions for c in scored)
            rows.append(f"| `{code}` | {config.RATES[code]:.0%} | {ds.quotas[code]} | {k} | {k / len(scored):.1%} |")
        return rows

    lines = [
        "# Synthetic dataset report",
        "",
        f"Generator v{GENERATOR_VERSION}, seed `{ds.seed}`, n `{ds.n}`, price tolerance "
        f"{config.PRICE_TOLERANCE_PCT}%, GST 10%",
        "",
        "## Files",
        "",
        f"- Invoice PDFs: **{n}**",
        f"- Distinct invoices: {len(originals)}",
        f"- Duplicates (same supplier + invoice number + total, different file): "
        f"{sum(c.duplicate_of is not None for c in ds.cases)}",
        f"- Exact re-sends (byte-identical file; ingestion must be a no-op): "
        f"{sum(c.exact_resend_of is not None for c in ds.cases)}",
        f"- Scored files (all except exact re-sends): {len(scored)}",
        "",
        "Rates are targets turned into exact counts (`Injected` = round(rate x n)). A duplicate",
        "carries the conditions of the invoice it copies, so a condition can appear on more files",
        "than it was injected into.",
        "",
        "## Flag reason codes",
        "",
        *table(config.FLAG_CODES),
        "",
        "## Conditions (must not flag on their own)",
        "",
        *table(config.CONDITION_CODES),
    ]

    status = Counter(("flag" if c.reason_codes else "recommend_approve") for c in scored)
    multi = sum(len(c.reason_codes) >= 2 for c in scored)
    variants = Counter(c.gst_truth["variant"] for c in ds.cases if "gst_miscalculated" in c.reason_codes)
    lines += [
        "",
        "## Expected outcomes",
        "",
        f"- `recommend_approve`: {status['recommend_approve']}",
        f"- `flag`: {status['flag']} ({multi} with two or more reason codes)",
        f"- ingestion no-op (exact re-send): {n - len(scored)}",
        "",
        "GST error variants: " + ", ".join(f"{k} {v}" for k, v in sorted(variants.items())),
        "",
        "## Coverage",
        "",
        "| Template | Files |",
        "|---|---:|",
    ]
    for t, k in sorted(Counter(suppliers[c.doc.supplier_key].template for c in ds.cases).items()):
        lines.append(f"| {t} | {k} |")
    lines += ["", "| Supplier | Category | Template | Files |", "|---|---|---|---:|"]
    per_supplier = Counter(c.doc.supplier_key for c in ds.cases)
    for s in ds.suppliers:
        lines.append(f"| {s.name} | {s.category} | {s.template} | {per_supplier[s.key]} |")

    n_lines = [len(c.doc.lines) for c in originals]
    mixed = sum(
        any(t.master_gst_applicable for t in c.line_truth) and not all(t.master_gst_applicable for t in c.line_truth)
        for c in originals
    )
    lines += [
        "",
        f"- Lines per invoice: min {min(n_lines)}, max {max(n_lines)}, mean {sum(n_lines) / len(n_lines):.1f}",
        f"- Invoices mixing GST-free and taxable lines: {mixed}",
        f"- Invoices with per-line GST rounding (template D): {sum(c.doc.gst_method == 'line' for c in originals)}",
        "",
        "## Master data (seed.sql)",
        "",
        f"- Suppliers: {len(ds.suppliers)}",
        f"- Purchase orders: {len(ds.purchase_orders)} "
        f"({len(ds.purchase_orders) - len({c.true_po_number for c in originals if c.true_po_number})} never invoiced)",
        f"- PO lines: {sum(len(po.lines) for po in ds.purchase_orders)}",
        f"- Receipts: {len(ds.receipts)}",
        "",
    ]
    return "\n".join(lines)


def _num(q: Decimal | None) -> float | int | None:
    if q is None:
        return None
    return int(q) if q == q.to_integral_value() else float(q)


def _lit(value: str | None) -> str:
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
