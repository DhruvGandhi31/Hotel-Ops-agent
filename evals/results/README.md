# Eval results

| File | What it is | Counts toward the P2 gate? |
|---|---|---|
| [`extraction-n8n-run1-seed42.md`](extraction-n8n-run1-seed42.md) | Gate run 1: seed 42, through the workflow with n8n's `Extract From File` | **No: it failed** on line items (86.8% against 90%) and was superseded after the cause was found |
| [`extraction-n8n.md`](extraction-n8n.md) | Gate run 2: seed 43 (a fresh dataset), through the workflow with the `pdf-text` service | **Yes: the P2 result. All targets met** (total, invoice number, PO number 100%; line items 99.2%) |
| [`dev/`](dev/) | Dev-set runs (seed 7) used to choose the model and find the cause of run 1's failure; also the P3 matcher dev run on seed 42 | No: tuning data |
| [`reconciliation-e2e-seed44.md`](reconciliation-e2e-seed44.md) | **P3 gate run**: seed 44 (fresh), end to end from the PDFs through the workflow, `qwen3.5:9b` | **Yes: the P3 result.** `flag` recall 100%, precision 98.6%; five invoices differ, all traced to extraction |
| [`reconciliation-matcher-seed44.md`](reconciliation-matcher-seed44.md) | P3 diagnostic, same data: ground-truth extraction, the real model matches the reworded lines | No: diagnostic, run after the gate number was fixed |
| [`reconciliation-rules-seed44.md`](reconciliation-rules-seed44.md) | P3 diagnostic, same data: ground-truth extraction and line matches, SQL only | No: diagnostic. One mismatch (`inv_0162`): a label the reconciler cannot meet |

## How the two gate runs relate

1. **Run 1** (seed 42) met all three header targets at 100% but missed line items: 762 of 878 lines
   (86.8%). The failure analysis in the decisions log shows 82% of the wrong lines were description
   errors, and quantity, unit price and line total were right on 97.8%.
2. **Diagnosis, on dev data.** The same 59 dev invoices score 248/248 lines with Python's `pypdf` text
   and 226/248 (91.1%) with n8n's text: the workflow's text extractor was the cause, not the model or
   the prompt.
3. **Fix.** A `pdf-text` service running the same `pypdf` code replaced `Extract From File`. With the
   prompt unchanged, the dev set through the workflow scored 248/248
   ([`dev/extraction-n8n-v1-pdftext.md`](dev/extraction-n8n-v1-pdftext.md)).
4. **Run 2** (seed 43) is the honest final number: the seed-42 failures had been analysed, so that
   set could no longer measure a fix derived from them. Seed 43 had never been examined.

Targets, prompt and model were identical across both runs; only the text source changed.

To reproduce run 2: `python data-gen/generate.py --n 200 --seed 43 --out data-gen/out-gate2`, then
`bash scripts/reset-ingestion.sh` and
`python evals/run.py --suite extraction --target n8n --data data-gen/out-gate2`. The dataset's label
distribution is in [`docs/synthetic-data-report-seed43.md`](../../docs/synthetic-data-report-seed43.md).

One line of `extraction-n8n.md` (the "Input text" label) was corrected by hand after the run, because
the report generator still named the old extractor; the generator is fixed and no number was touched.

Every run, including the discarded ones and why they were discarded, is listed in
[`docs/p2-engineering-log.md`](../../docs/p2-engineering-log.md#2-every-model-run).

Raw per-invoice output lives in `raw/` and `dev/raw/` and is gitignored.

## P3 (reconciliation)

The gate run is `reconciliation-e2e-seed44.md`; the two other seed-44 reports were taken afterwards on the
same data to separate what the rules, the model's line matching and the extraction each cost. The story
(each of the five differing invoices, with its cause) is in
[`docs/p3-engineering-log.md`](../../docs/p3-engineering-log.md#26-the-gate-run-end-to-end-from-the-pdfs-seed-44-fresh-never-examined-real-qwen359b).
To reproduce: `python data-gen/generate.py --n 200 --seed 44 --out data-gen/out-gate3`, then
`bash scripts/reset-ops-data.sh && bash scripts/seed.sh data-gen/out-gate3/seed.sql`,
`bash scripts/reset-ingestion.sh` and
`python evals/run.py --suite reconciliation --source pdf --data data-gen/out-gate3 --tag seed44`.
