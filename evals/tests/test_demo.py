"""The demo's guards (it is otherwise exercised live by hand and by evals/pipeline_check.py's form section)."""

import pytest

import demo


@pytest.fixture()
def env(monkeypatch):
    for key, value in {
        "INGEST_WEBHOOK_TOKEN": "t",
        "N8N_OWNER_EMAIL": "o@example.com",
        "N8N_OWNER_PASSWORD": "Pw1-x",
    }.items():
        monkeypatch.setenv(key, value)


def test_it_needs_its_environment(monkeypatch, capsys):
    for key in ("INGEST_WEBHOOK_TOKEN", "N8N_OWNER_EMAIL", "N8N_OWNER_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    assert demo.main([]) == 2
    assert "INGEST_WEBHOOK_TOKEN" in capsys.readouterr().err


def test_it_refuses_a_database_loaded_with_another_datasets_master_data(env, monkeypatch, tmp_path, capsys):
    (tmp_path / "seed.sql").write_text("INSERT ... 'dataset_fingerprint', '" + "a" * 64 + "' ...", encoding="utf-8")
    monkeypatch.setattr(demo, "loaded_fingerprint", lambda: "b" * 64)
    uploaded = []
    monkeypatch.setattr(demo, "upload_invoice", lambda *a, **k: uploaded.append(a) or (200, {}))
    assert demo.main(["--data", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "supplier unknown" in err and "reset-ops-data.sh" in err and "seed.sh" in err
    assert uploaded == [], "nothing may be uploaded when the master data does not match"


def test_an_explicit_file_skips_the_dataset_check(env, monkeypatch, tmp_path, capsys):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(demo, "loaded_fingerprint", lambda: pytest.fail("must not be consulted for --file"))
    monkeypatch.setattr(demo, "upload_invoice", lambda *a, **k: (200, {"status": "noop"}))
    assert demo.main(["--file", str(pdf)]) == 1  # got past the guard; "noop" is not a new invoice
    assert "no new invoice" in capsys.readouterr().err
