# Dev-set runs: choosing the model

Extraction only (`run.py --target ollama`), on the **dev set** (seed 7, 60 invoices, 59 scored),
with the final prompt and schema. These were used to choose the model; they are **not** the
gate result (that is [`../extraction-n8n.md`](../extraction-n8n.md), seed 42, through the
workflow). Each model was run twice: with decoding constrained to the JSON schema (`schema`) and
with Ollama's generic JSON mode (`json`), because n8n's Ollama node offers only the latter.

| Model | Mode | total | invoice no. | PO no. | Line items | Fully clean | needs_review | Median latency |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `qwen3.5:9b` | schema | 100% | 100% | 100% | 100% (248/248) | 100% | 0 | 15.8 s |
| `qwen3.5:9b` | json | 100% | 100% | 100% | 100% (248/248) | 100% | 0 | 15.9 s |
| `gemma4:e4b` | schema | 100% | 100% | 100% | 99.2% (246/248) | 96.6% | 0 | 4.8 s |
| `gemma4:e4b` | json | 100% | 100% | 100% | 99.2% (246/248) | 96.6% | 0 | 4.8 s |
| `qwen3.5:4b` | schema | 98.3% | 100% | 100% | 93.5% (232/248) | 81.4% | 0 | 7.7 s |
| `qwen3.5:4b` | json | 94.9% | 100% | 100% | 92.7% (230/248) | 81.4% | 2 | 7.7 s |

(`Fully clean` means every field and every line correct. Latency is on an RTX 4060 laptop GPU, 8 GB,
context 4096.)

What it shows:

- **`qwen3.5:9b` is the only model with no misses**, so it is the P2 model. `gemma4:e4b` is a
  credible fast alternative (3x quicker, two line-item misses).
- **JSON mode equals schema mode for the 9B and gemma**: the raw replies were byte-identical on
  all 59 invoices. For the 4B they differ: JSON mode sent two invoices to `needs_review` that
  schema mode handled. So n8n's chat model node is enough here, but a weaker model would need
  schema-constrained decoding (an HTTP Request node to Ollama).
- Dev results were discarded and regenerated once after a bug was found in how the schema was
  embedded in the prompt (see `docs/p2-engineering-log.md`, B3); the table above is the corrected run.

The per-model reports in this directory have the full field tables and failures. Raw per-invoice
output is in `raw/` (gitignored).

## Through the workflow: the text-source finding

The runs above call Ollama directly with Python's text. Running the **same 59 dev invoices through the
workflow** (`run.py --target n8n`, same model and prompt) shows what the text extractor does:

| Text source | Line items | Invoices fully clean | Report |
|---|---:|---:|---|
| Python `pypdf` (direct harness) | 100% (248/248) | 100% | `extraction-qwen3-5-9b-json.md` |
| n8n `Extract From File` | **91.1% (226/248)** | 86.4% | `extraction-n8n-v0.md` |
| `pdf-text` service (pypdf) | 100% (248/248) | 100% | `extraction-n8n-v1-pdftext.md` |

Header fields were 100% in all three. n8n's extractor space-joins every cell of a table row, so the
model cannot tell where a description ends or which trailing number is the line total and which the
GST. That finding is why `Extract From File` was replaced; see `docs/p2-engineering-log.md`.
