"""The dashboard's page: figures shown as given, database text escaped, empty data handled."""

import dashboard

DATA = {
    "invoices": 12,
    "duplicates": 2,
    "recommendations": {"flag": 5, "recommend_approve": 6, "needs_review": 1},
    "reasons": {"price_variance": 3, "<script>alert(1)</script>": 2},
    "approvals": {"pending": 4, "approved": 7, "rejected": 1},
    "overrides": 3,
    "errors": 0,
    "audit_rows": 99,
    "recent": [
        {"at": "2026-10-09 01:02:03", "actor": "<b>mallory</b>", "action": "approval.decided",
         "entity_type": "invoice", "entity_id": "7"},
    ],
}  # fmt: skip


def test_figures_are_shown():
    page = dashboard.render(DATA, "now")
    for text in ("Invoices ingested", "Reconciled", "Flags overridden", "price_variance", "approval.decided"):
        assert text in page
    assert "<div class=big>12</div>Invoices ingested" in page
    assert "<div class=big>12</div>Reconciled" in page  # 5 + 6 + 1


def test_database_text_is_escaped():
    page = dashboard.render(DATA, "now")
    assert "<script>" not in page and "<b>mallory</b>" not in page
    assert "&lt;script&gt;" in page and "&lt;b&gt;mallory&lt;/b&gt;" in page


def test_an_empty_database_renders():
    empty = {**DATA, "invoices": 0, "duplicates": 0, "recommendations": {}, "reasons": {}, "approvals": {},
             "overrides": 0, "recent": []}  # fmt: skip
    page = dashboard.render(empty, "now")
    assert page.count("nothing yet") == 3
    assert "<div class=big>0</div>Waiting for a person" in page


def test_bars_are_scaled_to_the_largest():
    out = dashboard.bars({"a": 10, "b": 5}, "#000")
    assert "width:100%" in out and "width:50%" in out
