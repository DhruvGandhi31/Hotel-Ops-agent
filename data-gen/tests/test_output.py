import re
from pathlib import Path

from pypdf import PdfReader

from hotel_datagen.abn import format_abn
from hotel_datagen.build import build_dataset
from hotel_datagen.money import format_money
from hotel_datagen.output import render_report, write_dataset


def _text(path) -> str:
    return re.sub(r"\s+", " ", " ".join(page.extract_text() for page in PdfReader(path).pages))


def test_same_seed_same_bytes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write_dataset(build_dataset(30, 7), a)
    write_dataset(build_dataset(30, 7), b)
    files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    for rel in files:
        assert (a / rel).read_bytes() == (b / rel).read_bytes(), rel


def test_different_seed_different_data(tmp_path):
    write_dataset(build_dataset(30, 7), tmp_path / "a")
    write_dataset(build_dataset(30, 8), tmp_path / "b")
    assert (tmp_path / "a" / "seed.sql").read_text() != (tmp_path / "b" / "seed.sql").read_text()


def test_pdf_text_layer_contains_the_extraction_targets(gate_out, ground_truths):
    """P2 extracts from the PDF text layer, so every target value must be in it."""
    for gt in ground_truths:
        doc = gt["document"]
        text = _text(gate_out / gt["file"])
        expected = [
            doc["invoice_number"],
            format_abn(doc["supplier_abn"]),
            format_money(doc["total_cents"]),
            format_money(doc["gst_cents"]),
            *(re.sub(r"\s+", " ", ln["description"]) for ln in doc["lines"]),
            *(format_money(ln["line_total_cents"], symbol=False) for ln in doc["lines"]),
        ]
        if doc["po_number"]:
            expected.append(doc["po_number"])
        missing = [value for value in expected if value not in text]
        assert not missing, (gt["file"], missing)


def test_seed_sql_covers_master_data(gate_out, master):
    sql = (gate_out / "seed.sql").read_text(encoding="utf-8")
    assert sql.count("INSERT INTO suppliers ") == len(master["suppliers"])
    assert sql.count("INSERT INTO purchase_orders ") == len(master["purchase_orders"])
    assert sql.count("INSERT INTO receipts ") == len(master["receipts"])
    assert sql.count("INSERT INTO receipt_lines ") == sum(len(r["lines"]) for r in master["receipts"])


def test_committed_report_is_current():
    """docs/synthetic-data-report.md is the P1 gate artefact; regenerate it when the generator changes:
    python data-gen/generate.py --n 200 --seed 42 && cp data-gen/out/REPORT.md docs/synthetic-data-report.md
    """
    committed = Path(__file__).parents[2] / "docs" / "synthetic-data-report.md"
    assert committed.read_text(encoding="utf-8") == render_report(build_dataset(200, 42))
