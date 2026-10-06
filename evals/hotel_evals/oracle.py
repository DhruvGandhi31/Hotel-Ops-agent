"""A perfect extractor: ground truth rendered in the extraction schema's wire format.

Feeding this through the scorer must give 100%, and through the validator must always pass;
both are tests. It is also the baseline that a stubbed model returns when exercising the
n8n workflow without an LLM.
"""

from decimal import Decimal


def dollars(cents: int | None) -> str | None:
    return None if cents is None else f"{Decimal(cents) / 100:.2f}"


def truth_to_extraction(doc: dict) -> dict:
    return {
        "supplier_name": doc["supplier_name"],
        "supplier_abn": doc["supplier_abn"],
        "bill_to_name": doc["bill_to_name"],
        "invoice_number": doc["invoice_number"],
        "invoice_date": doc["invoice_date"],
        "due_date": doc["due_date"],
        "po_number": doc["po_number"],
        "currency": doc["currency"],
        "lines": [
            {
                "supplier_code": ln["supplier_code"],
                "description": ln["description"],
                "quantity": ln["quantity"],
                "unit": ln["unit"],
                "unit_price": dollars(ln["unit_price_cents"]),
                "gst_applicable": ln["gst_applicable"],
                "line_total": dollars(ln["line_total_cents"]),
                "line_gst": dollars(ln["gst_cents"]),
            }
            for ln in doc["lines"]
        ],
        "subtotal": dollars(doc["subtotal_cents"]),
        "gst": dollars(doc["gst_cents"]),
        "total": dollars(doc["total_cents"]),
    }
