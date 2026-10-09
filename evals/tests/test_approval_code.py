"""The Approval Form's Code nodes (workflows/approval_form.json), run under Node.

These build the pages an approver reads from database text that came off a supplier's PDF through a model, so the
main thing under test is that none of it can become markup. They also pin the decision mapping and the request
parsing, and check that the form's pinned id agrees everywhere it is used.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from hotel_evals.approval_client import APPROVAL_FORM_ID

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "workflows" / "approval_form.json"
HARNESS = ROOT / "evals" / "js" / "run_code_node.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

EVIL = '<script>alert(1)</script><img src=x onerror="alert(2)"> & "q" \'s\''
USER = {"id": "u-7", "email": "ada@example.com", "firstName": "Ada", "lastName": "Approver"}


def run_node(node: str, cases: list[dict]) -> list[dict]:
    request = {"workflow": str(WORKFLOW), "node": node, "root": str(ROOT), "cases": cases}
    proc = subprocess.run(
        ["node", str(HARNESS)], input=json.dumps(request), capture_output=True, text=True, timeout=120, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def run_one(node: str, case: dict) -> dict:
    out = run_node(node, [case])[0]
    assert "error" not in out, out
    return out["output"][0]["json"]


def node_code(name: str) -> str:
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    return next(n for n in wf["nodes"] if n["name"] == name)["parameters"]["jsCode"]


def assert_no_markup(html: str) -> None:
    """Hostile text was escaped: no tag from EVIL survived as markup, and the text itself is still there."""
    assert "<script" not in html and "<img" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# ---- request parsing ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("form_input", "kind"),
    [
        ({"user": USER}, "list"),
        ({"user": USER, "approval_id": ""}, "list"),
        ({"user": USER, "approval_id": "  "}, "list"),
        ({"user": USER, "approval_id": "5"}, "decide"),
        ({"user": USER, "approval_id": " 12 "}, "decide"),
        ({"user": USER, "approval_id": "0"}, "bad_id"),
        ({"user": USER, "approval_id": "-3"}, "bad_id"),
        ({"user": USER, "approval_id": "05"}, "bad_id"),
        ({"user": USER, "approval_id": "1;DROP TABLE approvals"}, "bad_id"),
        ({"user": USER, "approval_id": "1.5"}, "bad_id"),
        ({"user": USER, "approval_id": "9" * 30}, "bad_id"),
        ({"approval_id": "5"}, "no_user"),
        ({"user": {"id": "u-1"}, "approval_id": "5"}, "no_user"),
        ({"user": None}, "no_user"),
    ],
)
def test_read_request(form_input, kind):
    assert run_one("Read Request", {"input": form_input})["kind"] == kind


def test_read_request_keeps_the_user_and_the_id_as_text():
    out = run_one("Read Request", {"input": {"user": USER, "approval_id": "5"}})
    assert out["approval_id"] == "5" and out["user"]["email"] == "ada@example.com"


# ---- the pending list -----------------------------------------------------------------------------


def pending(items, total=None, shown=None):
    return {
        "result": {
            "total": total if total is not None else len(items),
            "shown": shown if shown is not None else len(items),
            "items": items,
        }
    }


def item(**kw):
    base = {
        "approval_id": 9,
        "invoice_id": 4,
        "recommended_status": "flag",
        "reason_codes": ["price_variance"],
        "review_reasons": [],
        "requested_at": "2026-10-08T17:51:03.123456+00:00",
        "invoice_number": "INV-1",
        "po_number": "PO-1",
        "total_cents": 123456,
        "supplier_name": "Fresh Foods",
    }
    return {**base, **kw}


def test_list_page_rows_links_and_formatting():
    html = run_one(
        "Build List Page",
        {
            "input": pending(
                [
                    item(),
                    item(
                        approval_id=10, recommended_status="recommend_approve", reason_codes=[], invoice_number="INV-2"
                    ),
                ]
            )
        },
    )["html"]
    assert f'href="/form/{APPROVAL_FORM_ID}?approval_id=9"' in html and 'approval_id=10"' in html
    assert "$1,234.56" in html and "FLAGGED" in html and "Recommended for approval" in html
    assert "2026-10-08 17:51 +00:00" in html
    assert "2 waiting." in html


def test_list_page_escapes_everything_from_the_database():
    html = run_one(
        "Build List Page", {"input": pending([item(invoice_number=EVIL, supplier_name=EVIL, reason_codes=[EVIL])])}
    )["html"]
    assert_no_markup(html)


def test_list_page_empty_and_truncated():
    empty = run_one("Build List Page", {"input": pending([])})["html"]
    assert "Nothing is waiting for approval." in empty and "<table" not in empty
    cut = run_one("Build List Page", {"input": pending([item()], total=250, shown=1)})["html"]
    assert "250 waiting, showing the first 1." in cut


# ---- the decision page ----------------------------------------------------------------------------


def detail(status="pending", recommended="flag", **kw):
    d = {
        "approval": {
            "id": 9,
            "status": status,
            "recommended_status": recommended,
            "reason_codes": ["price_variance"],
            "review_reasons": [],
            "requested_at": "2026-10-08T17:51:03+00:00",
            "decided_at": None,
            "decided_by": None,
            "comment": None,
        },
        "invoice": {
            "id": 4,
            "invoice_number": "INV-1",
            "invoice_date": "2026-09-10",
            "due_date": "2026-09-24",
            "po_number": "PO-1",
            "supplier_name": "Fresh Foods",
            "supplier_abn": "11111111111",
            "subtotal_cents": 10000,
            "gst_cents": 1000,
            "total_cents": 11000,
            "duplicate_of_invoice_number": None,
        },
        "purchase_order": "PO-1",
        "gst": {"basis": "po", "stated_cents": 1000, "expected_cents": 1000, "tolerance_cents": 1},
        "lines": [
            {
                "line_no": 1,
                "description": "Tomatoes",
                "quantity": 2,
                "unit": "box",
                "unit_price_cents": 5101,
                "line_total_cents": 10202,
                "match_method": "sku",
                "match_confidence": None,
                "po_line_no": 3,
                "po_description": "Tomatoes Roma",
                "po_quantity": 2,
                "po_unit_price_cents": 5000,
                "received_quantity": 2,
                "billed_before": 0,
                "price_variance_pct": 2.02,
                "issues": ["price_variance"],
            }
        ],
    }
    for k, v in kw.items():
        d[k] = v
    return d


def decision_case(d, approval_id="9"):
    return {
        "input": {"detail": d},
        "nodes": {"Read Request": {"json": {"kind": "decide", "approval_id": approval_id, "user": USER}}},
    }


def test_open_decision_page_shows_the_evidence():
    out = run_one("Build Decision Page", decision_case(detail()))
    assert out["state"] == "open" and out["needs_comment"] is True
    html = out["html"]
    for expected in (
        "FLAGGED: invoice INV-1",
        "ada@example.com",
        "price_variance",
        "Fresh Foods",
        "ABN 11111111111",
        "$51.01",
        "$50.00",
        "PO line 3",
        "price above PO",
        "received 2",
        "Give a reason",
    ):
        assert expected in html, expected


def test_a_clean_recommendation_does_not_require_a_reason():
    out = run_one("Build Decision Page", decision_case(detail(recommended="recommend_approve")))
    assert out["needs_comment"] is False and "optional for approving" in out["html"]


def test_decision_page_escapes_database_text_and_unknown_reason_codes():
    d = detail()
    d["invoice"]["supplier_name"] = EVIL
    d["invoice"]["invoice_number"] = EVIL
    d["lines"][0]["description"] = EVIL
    d["lines"][0]["po_description"] = EVIL
    d["lines"][0]["issues"] = [EVIL]
    d["approval"]["reason_codes"] = [EVIL]
    assert_no_markup(run_one("Build Decision Page", decision_case(d))["html"])


def test_missing_and_already_decided_approvals():
    missing = run_one("Build Decision Page", decision_case(None, approval_id="77"))
    assert missing["state"] == "missing" and "77" in missing["message"]
    d = detail(status="approved")
    d["approval"].update({"decided_by": "bob@example.com", "decided_at": "2026-10-08T18:00:00+00:00", "comment": EVIL})
    out = run_one("Build Decision Page", decision_case(d))
    assert out["state"] == "decided" and "bob@example.com" in out["message"] and "2026-10-08 18:00" in out["message"]
    assert "<script" not in out["message"] and "&lt;script&gt;" in out["message"]


def test_unmatched_line_and_duplicate_are_described():
    d = detail()
    d["lines"][0].update({"po_line_no": None, "po_description": None, "issues": ["unmatched_line"]})
    d["invoice"]["duplicate_of_invoice_number"] = "INV-0"
    html = run_one("Build Decision Page", decision_case(d))["html"]
    assert "not matched to a PO line" in html and "no PO line" in html and "Duplicate of" in html and "INV-0" in html


@pytest.mark.parametrize(
    ("cents", "text"),
    [(0, "$0.00"), (5, "$0.05"), (100, "$1.00"), (123456, "$1,234.56"), (-250, "-$2.50"), (100000000, "$1,000,000.00")],
)
def test_money_formatting(cents, text):
    d = detail()
    d["invoice"]["total_cents"] = cents
    assert text in run_one("Build Decision Page", decision_case(d))["html"]


# ---- the decision itself --------------------------------------------------------------------------


def prepare_case(answer):
    return {
        "input": answer,
        "nodes": {"Read Request": {"json": {"kind": "decide", "approval_id": "9", "user": USER}}},
        "execution": {"id": "e-42"},
        "workflow": {"id": "wf-1", "name": "Approval Form"},
    }


@pytest.mark.parametrize(
    ("choice", "decision"),
    [("Approve", "approved"), ("Reject", "rejected"), ("approve", "approved"), (" REJECT ", "rejected")],
)
def test_prepare_decision_maps_the_choice(choice, decision):
    out = run_one("Prepare Decision", prepare_case({"Decision": choice, "Comment": "because"}))
    assert out["abandoned"] is False and out["decision"] == decision
    assert out["approver"] == "ada@example.com" and out["approval_id"] == "9" and out["comment"] == "because"
    assert out["context"] == {
        "user_id": "u-7",
        "execution_id": "e-42",
        "workflow_id": "wf-1",
        "workflow_name": "Approval Form",
    }


@pytest.mark.parametrize("answer", [{}, {"Decision": ""}, {"Decision": "Maybe"}, {"Decision": None}, {"Comment": "x"}])
def test_no_valid_choice_means_nothing_is_recorded(answer):
    assert run_one("Prepare Decision", prepare_case(answer)) == {"abandoned": True}


def test_the_approver_always_comes_from_the_signed_in_user_never_from_the_form():
    out = run_one(
        "Prepare Decision",
        prepare_case(
            {"Decision": "Approve", "approver": "mallory@example.com", "user": {"email": "mallory@example.com"}}
        ),
    )
    assert out["approver"] == "ada@example.com"


@pytest.mark.parametrize(
    ("result", "title_part", "message_part"),
    [
        (
            {"result": "recorded", "approval_id": 9, "decision": "approved", "decided_by": "ada@example.com"},
            "Recorded: approved",
            "ada@example.com",
        ),
        ({"result": "comment_required"}, "reason is required", "Nothing was recorded"),
        (
            {
                "result": "already_decided",
                "decision": "rejected",
                "decided_by": "bob@example.com",
                "decided_at": "2026-10-08T18:00:00+00:00",
            },
            "already rejected",
            "bob@example.com",
        ),
        ({"result": "not_found"}, "not found", "no approval number 9"),
        ({"result": "invalid", "reason": EVIL}, "Not recorded", "&lt;script&gt;"),
    ],
)
def test_result_pages(result, title_part, message_part):
    out = run_one(
        "Build Result Page",
        {"input": {"result": result}, "nodes": {"Prepare Decision": {"json": {"approval_id": "9"}}}},
    )
    assert title_part in out["title"] and message_part in out["message"]
    assert "<script" not in out["message"]


def test_info_pages():
    for kind, title in (("no_user", "Sign-in required"), ("bad_id", "Invalid link")):
        assert run_one("Build Info Page", {"nodes": {"Read Request": {"json": {"kind": kind}}}})["title"] == title


# ---- consistency across files ---------------------------------------------------------------------


def test_the_pinned_form_id_is_the_same_everywhere():
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    trigger = next(n for n in wf["nodes"] if n["name"] == "Approvals Form")
    assert trigger["webhookId"] == APPROVAL_FORM_ID
    assert trigger["parameters"]["authentication"] == "n8nUserAuth"
    for name in ("Build List Page", "Build Decision Page", "Build Result Page", "Build Info Page"):
        assert f"const FORM_PATH = '/form/{APPROVAL_FORM_ID}';" in node_code(name)


def _helpers(code: str) -> str:
    return re.search(r"// >>> page-helpers.*?// <<< page-helpers", code, re.S).group(0)


def test_every_page_node_carries_the_identical_helper_block():
    blocks = {
        name: _helpers(node_code(name))
        for name in ("Build List Page", "Build Decision Page", "Build Result Page", "Build Info Page")
    }
    assert len(set(blocks.values())) == 1 and len(next(iter(blocks.values()))) > 1500


def test_every_reason_code_the_database_can_produce_has_text():
    migration = (ROOT / "db" / "migrations" / "006_reconciliation.sql").read_text(encoding="utf-8")
    codes = set(re.findall(r"'([a-z_]+)'", re.search(r"reason_codes <@ ARRAY\[(.*?)\]", migration, re.S).group(1)))
    codes |= set(re.findall(r"'([a-z_]+)'", re.search(r"review_reasons <@ ARRAY\[(.*?)\]", migration, re.S).group(1)))
    assert len(codes) == 14
    helpers = _helpers(node_code("Build Decision Page"))
    missing = [c for c in sorted(codes) if f"  {c}: '" not in helpers]
    assert not missing, f"no explanation text for {missing}"


def test_the_decision_pages_and_the_reconciliation_hand_off_exist():
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    names = {n["name"] for n in wf["nodes"]}
    assert {"Decision Page", "Decision Page With Reason", "Record Decision", "Prepare Decision"} <= names
    assert wf["settings"]["errorWorkflow"] == "hoaErrorHandler01"
    # the comment is required on the page for flagged and doubtful invoices
    with_reason = next(n for n in wf["nodes"] if n["name"] == "Decision Page With Reason")
    comment = next(f for f in with_reason["parameters"]["formFields"]["values"] if f["fieldLabel"] == "Comment")
    assert comment["requiredField"] is True
    # an abandoned page times out instead of waiting forever (this is a top-level parameter, not an option)
    for name in ("Decision Page", "Decision Page With Reason", "Pending List"):
        params = next(n for n in wf["nodes"] if n["name"] == name)["parameters"]
        assert params["limitWaitTime"] is True and params["resumeAmount"] == 30 and params["resumeUnit"] == "minutes"


# ---- after the first real-browser run: layout, a way back, button labels ------------------------------


def test_the_list_is_a_list_not_a_table_that_overflows_the_card():
    html = run_one("Build List Page", {"input": pending([item(), item(approval_id=10, invoice_number="INV-2")])})[
        "html"
    ]
    assert "<ul>" in html and html.count("<li>") == 2 and "<table" not in html
    assert f'<a href="/form/{APPROVAL_FORM_ID}?approval_id=9">Review and decide</a>' in html
    assert "Reasons: price_variance" in html and "FLAGGED</b>: invoice INV-1 from Fresh Foods, $1,234.56" in html


def test_a_list_entry_without_reasons_has_no_empty_reasons_line():
    html = run_one(
        "Build List Page", {"input": pending([item(recommended_status="recommend_approve", reason_codes=[])])}
    )["html"]
    assert "Reasons:" not in html


BACK = f'<a href="/form/{APPROVAL_FORM_ID}">Back to the pending approvals</a>'


def test_every_page_that_ends_a_session_offers_the_way_back():
    nodes = {"Prepare Decision": {"json": {"approval_id": "9"}}}
    for result in (
        {"result": "recorded", "approval_id": 9, "decision": "approved", "decided_by": "ada@example.com"},
        {"result": "comment_required"},
        {
            "result": "already_decided",
            "decision": "approved",
            "decided_by": "b@example.com",
            "decided_at": "2026-10-08T18:00:00+00:00",
        },
        {"result": "not_found"},
        {"result": "invalid", "reason": "x"},
    ):
        assert BACK in run_one("Build Result Page", {"input": {"result": result}, "nodes": nodes})["message"], result
    assert BACK in run_one("Build Decision Page", decision_case(None, approval_id="77"))["message"]
    decided = detail(status="approved")
    decided["approval"].update({"decided_by": "b@example.com", "decided_at": "2026-10-08T18:00:00+00:00"})
    assert BACK in run_one("Build Decision Page", decision_case(decided))["message"]
    assert BACK in run_one("Build Info Page", {"nodes": {"Read Request": {"json": {"kind": "bad_id"}}}})["message"]


def test_the_buttons_say_what_they_do():
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    labels = {
        n["name"]: n["parameters"]["options"].get("buttonLabel") for n in wf["nodes"] if n["type"].endswith(".form")
    }
    assert labels["Pending List"] == "Close"
    assert labels["Decision Page"] == labels["Decision Page With Reason"] == "Record decision"
