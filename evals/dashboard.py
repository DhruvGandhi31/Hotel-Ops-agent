"""A one-page operations dashboard: what the system has done, read from the ops database.

    python evals/dashboard.py                  # writes dashboard.html
    python evals/dashboard.py --out some.html

A static snapshot (self-contained HTML, no scripts, no network). Every figure is a count over tables the
workflows already write; nothing here measures model tokens or cost, which the workflows do not record.
"""

import argparse
import html
import json
from datetime import UTC, datetime
from pathlib import Path

from hotel_evals.ops_db import psql

QUERY = """
SELECT json_build_object(
  'invoices',        (SELECT count(*) FROM invoices),
  'duplicates',      (SELECT count(*) FROM invoices WHERE duplicate_of IS NOT NULL),
  'recommendations', (SELECT coalesce(json_object_agg(status, n), '{}') FROM
                        (SELECT status, count(*) n FROM reconciliations GROUP BY status) s),
  'reasons',         (SELECT coalesce(json_object_agg(code, n), '{}') FROM
                        (SELECT code, count(*) n FROM reconciliations, unnest(reason_codes || review_reasons) code
                         GROUP BY code) r),
  'approvals',       (SELECT coalesce(json_object_agg(status, n), '{}') FROM
                        (SELECT status, count(*) n FROM approvals GROUP BY status) a),
  'overrides',       (SELECT count(*) FROM approvals
                        WHERE status = 'approved' AND recommended_status <> 'recommend_approve'),
  'errors',          (SELECT count(*) FROM audit_log WHERE action = 'workflow.error'),
  'audit_rows',      (SELECT count(*) FROM audit_log),
  'recent',          (SELECT coalesce(json_agg(t), '[]') FROM
                        (SELECT to_char(occurred_at, 'YYYY-MM-DD HH24:MI:SS') AS at, actor, action,
                                entity_type, entity_id
                         FROM audit_log ORDER BY id DESC LIMIT 12) t)
)
"""


def e(value) -> str:
    return html.escape(str(value), quote=True)


def bars(counts: dict[str, int], colour: str) -> str:
    if not counts:
        return "<p class=muted>nothing yet</p>"
    top = max(counts.values())
    rows = [
        f"<div class=row><span class=label>{e(k)}</span>"
        f"<span class=bar style='width:{max(2, round(100 * v / top))}%;background:{colour}'></span>"
        f"<span class=n>{e(v)}</span></div>"
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return "".join(rows)


def render(data: dict, generated: str) -> str:
    rec, appr = data["recommendations"], data["approvals"]
    reconciled = sum(rec.values())
    events = "".join(
        f"<tr><td>{e(r['at'])}</td><td>{e(r['actor'])}</td><td>{e(r['action'])}</td>"
        f"<td>{e(r['entity_type'] or '')} {e(r['entity_id'] or '')}</td></tr>"
        for r in data["recent"]
    )
    cards = [
        ("Invoices ingested", data["invoices"]),
        ("Duplicates linked", data["duplicates"]),
        ("Reconciled", reconciled),
        ("Waiting for a person", appr.get("pending", 0)),
        ("Approved by a person", appr.get("approved", 0)),
        ("Rejected by a person", appr.get("rejected", 0)),
        ("Flags overridden", data["overrides"]),
        ("Workflow errors", data["errors"]),
    ]
    tiles = "".join(f"<div class=card><div class=big>{e(v)}</div>{e(k)}</div>" for k, v in cards)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hotel ops agent - dashboard</title>
<style>
body{{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1c2330}}
main{{max-width:960px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px}}
.muted{{color:#667}} .cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}}
.card{{background:#fff;border:1px solid #dde;border-radius:8px;padding:12px}} .big{{font-size:28px;font-weight:600}}
.panel{{background:#fff;border:1px solid #dde;border-radius:8px;padding:12px}}
.row{{display:flex;align-items:center;gap:8px;margin:4px 0}} .label{{width:170px;flex:none}}
.bar{{height:14px;border-radius:3px;display:inline-block}} .n{{font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
td,th{{text-align:left;padding:4px 6px;border-bottom:1px solid #eef}}
</style></head><body><main>
<h1>Hotel ops agent</h1>
<div class=muted>Snapshot of the ops database at {e(generated)}. {e(data["audit_rows"])} audit rows in total.</div>
<h2>Totals</h2><div class=cards>{tiles}</div>
<h2>What the rules recommended ({e(reconciled)} invoices)</h2><div class=panel>{bars(rec, "#3b82f6")}</div>
<h2>Why: reason codes and review reasons</h2><div class=panel>{bars(data["reasons"], "#f59e0b")}</div>
<h2>What people decided</h2><div class=panel>{bars(appr, "#10b981")}</div>
<p class=muted>Workflow errors are failures the Error Handler recorded. Many come from the outage tests,
which stop the model server on purpose; the audit log also survives data resets, so counts can span several runs.</p>
<h2>Latest audit events</h2><div class=panel><table>
<tr><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Entity</th></tr>{events}
</table></div>
</main></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("dashboard.html"))
    args = ap.parse_args(argv)
    data = json.loads(psql(QUERY))
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    args.out.write_text(render(data, stamp), encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
