"""Labels must be derivable from the written data alone.

The oracle below re-derives every reason code and condition from ground_truth/*.json and
master_data.json using plain rules, without looking at how the generator injected them.
If the generator ever mislabels a case, these tests catch it.
"""

import json
from collections import Counter
from decimal import Decimal

import pytest

from hotel_datagen import config
from hotel_datagen.build import build_dataset
from hotel_datagen.money import gst_cents, gst_tolerance_cents, line_total_cents
from hotel_datagen.output import ground_truth, master_data


def _canon_po(po: str) -> str:
    return po.upper().replace("-", "")


def _oracle(ground_truths: list[dict], master: dict) -> dict[str, tuple[set, set]]:
    pos = {_canon_po(po["po_number"]): po for po in master["purchase_orders"]}
    received = {
        (r["po_number"], ln["po_line_no"]): Decimal(str(ln["quantity_received"]))
        for r in master["receipts"]
        for ln in r["lines"]
    }
    tol = config.PRICE_TOLERANCE_PCT
    seen: dict[str, str] = {}
    derived = {}

    for gt in sorted(ground_truths, key=lambda g: g["file"]):  # arrival order
        doc, truth = gt["document"], gt["labels"]
        codes, conds = set(), set()

        if gt["invoice_key"] in seen:
            if seen[gt["invoice_key"]] == gt["file_sha256"]:
                conds.add("exact_resend")
            else:
                codes.add("duplicate_invoice")
        else:
            seen[gt["invoice_key"]] = gt["file_sha256"]

        po = None
        if doc["po_number"] is None:
            codes.add("missing_po")
        elif _canon_po(doc["po_number"]) not in pos:
            codes.add("unknown_po")
        else:
            po = pos[_canon_po(doc["po_number"])]

        master_gst = [t["master_gst_applicable"] for t in truth["lines"]]
        if po:
            for line, t in zip(doc["lines"], truth["lines"], strict=True):
                pol = po["lines"][t["po_line_no"] - 1]
                master_gst[line["line_no"] - 1] = pol["gst_applicable"]
                po_price, inv_price = pol["unit_price_cents"], line["unit_price_cents"]
                if abs(inv_price - po_price) * 100 > tol * po_price:
                    codes.add("price_variance")
                elif inv_price != po_price:
                    conds.add("price_within_tolerance")
                got = received[(po["po_number"], pol["line_no"])]
                qty = Decimal(str(line["quantity"]))
                if qty > got:
                    codes.add("short_delivery")
                elif got < Decimal(str(pol["quantity"])):
                    conds.add("partial_delivery")
                if line["description"] != pol["description"]:
                    conds.add("description_mismatch")

        taxable = sum(ln["line_total_cents"] for ln, g in zip(doc["lines"], master_gst, strict=True) if g)
        if abs(doc["gst_cents"] - gst_cents(taxable)) > gst_tolerance_cents(sum(master_gst)):
            codes.add("gst_miscalculated")

        derived[gt["file"]] = (codes, conds)
    return derived


def test_labels_match_independent_oracle(ground_truths, master):
    derived = _oracle(ground_truths, master)
    mismatches = []
    for gt in ground_truths:
        codes, conds = derived[gt["file"]]
        if codes != set(gt["labels"]["reason_codes"]) or conds != set(gt["labels"]["conditions"]):
            mismatches.append(
                (gt["file"], sorted(codes), gt["labels"]["reason_codes"], sorted(conds), gt["labels"]["conditions"])
            )
    assert not mismatches, mismatches


def test_expected_status_follows_reason_codes(ground_truths):
    for gt in ground_truths:
        labels = gt["labels"]
        if "exact_resend" in labels["conditions"]:
            assert labels["expected_status"] is None and labels["expected_ingestion"] == "noop"
        else:
            expected = "flag" if labels["reason_codes"] else "recommend_approve"
            assert labels["expected_status"] == expected and labels["expected_ingestion"] == "insert"


def test_documents_are_internally_consistent(ground_truths):
    for gt in ground_truths:
        doc = gt["document"]
        for ln in doc["lines"]:
            assert ln["line_total_cents"] == line_total_cents(Decimal(str(ln["quantity"])), ln["unit_price_cents"]), gt[
                "file"
            ]
        assert doc["subtotal_cents"] == sum(ln["line_total_cents"] for ln in doc["lines"]), gt["file"]
        assert doc["total_cents"] == doc["subtotal_cents"] + doc["gst_cents"], gt["file"]
        if gt["labels"]["gst"]["method"] == "line" and "gst_miscalculated" not in gt["labels"]["reason_codes"]:
            assert doc["gst_cents"] == sum(ln["gst_cents"] for ln in doc["lines"]), gt["file"]


def test_copies_arrive_after_their_original(ground_truths):
    by_file = {gt["file"].removeprefix("invoices/").removesuffix(".pdf"): gt for gt in ground_truths}
    for file_id, gt in by_file.items():
        src = gt["labels"]["duplicate_of"] or gt["labels"]["exact_resend_of"]
        if src:
            assert src < file_id
            assert by_file[src]["invoice_key"] == gt["invoice_key"]
            same_bytes = by_file[src]["file_sha256"] == gt["file_sha256"]
            assert same_bytes == (gt["labels"]["exact_resend_of"] is not None)


def test_injected_counts_match_quotas(ground_truths, manifest):
    quotas = manifest["quotas"]
    assert len(ground_truths) == manifest["n"]
    originals = [g for g in ground_truths if not g["labels"]["duplicate_of"] and not g["labels"]["exact_resend_of"]]
    injected = Counter(code for g in originals for code in g["labels"]["reason_codes"] + g["labels"]["conditions"])
    injected["duplicate_invoice"] = sum(bool(g["labels"]["duplicate_of"]) for g in ground_truths)
    injected["exact_resend"] = sum(bool(g["labels"]["exact_resend_of"]) for g in ground_truths)
    assert dict(injected) == {k: v for k, v in quotas.items() if v}


def test_every_label_and_template_is_represented(ground_truths):
    seen = Counter(c for g in ground_truths for c in g["labels"]["reason_codes"] + g["labels"]["conditions"])
    for code in config.FLAG_CODES + config.CONDITION_CODES:
        assert seen[code] > 0, code
    assert {g["template"] for g in ground_truths} == {"A", "B", "C", "D"}
    assert {g["labels"]["gst"]["variant"] for g in ground_truths} >= {
        "arithmetic_error",
        "gst_on_gst_free",
        "wrong_rate",
    }


@pytest.mark.parametrize("n", [50, 200, 500])
@pytest.mark.parametrize("seed", range(10))
def test_labels_hold_across_seeds(n, seed):
    """Builder edge cases show up only for some seeds, so check labels on many datasets.

    Skips PDF rendering: file hashes are stood in by the id of the case whose bytes a file
    would carry (exact re-sends share their original's bytes).
    """
    ds = build_dataset(n, seed)
    by_case = {c.case_id: c for c in ds.cases}
    suppliers = {s.key: s for s in ds.suppliers}

    def fake_sha(c):
        return f"bytes-of-{c.exact_resend_of if c.exact_resend_of is not None else c.case_id}"

    gts = [ground_truth(c, suppliers[c.doc.supplier_key], by_case, fake_sha(c)) for c in ds.cases]
    derived = _oracle(gts, json.loads(json.dumps(master_data(ds))))
    for gt in gts:
        codes, conds = derived[gt["file"]]
        assert codes == set(gt["labels"]["reason_codes"]), gt["file"]
        assert conds == set(gt["labels"]["conditions"]), gt["file"]
