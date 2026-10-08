"""Talk to the ops database through the running Postgres container (no driver needed)."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DatabaseError(RuntimeError):
    pass


def psql(sql: str, timeout: float = 900) -> str:
    """Run SQL in the ops database, return stdout (tuples only, unaligned). Raises on any SQL error."""
    proc = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "sh", "-c",
         'psql -X -tA -v ON_ERROR_STOP=1 -U "${OPS_DB_USER:-postgres}" -d ops'],
        input=sql, capture_output=True, text=True, cwd=ROOT, timeout=timeout, check=False,
    )  # fmt: skip
    if proc.returncode != 0:
        raise DatabaseError(proc.stderr.strip()[-800:] or f"psql exited {proc.returncode}")
    return proc.stdout


def dollar_quote(text: str, tag: str = "j") -> str:
    """Quote text for SQL without any escaping: $j$...$j$, picking a tag that is not in the text."""
    while f"${tag}$" in text:
        tag += "x"
    return f"${tag}${text}${tag}$"


def dataset_fingerprint(seed_sql: Path) -> str:
    """The fingerprint seed.sql records for its master data."""
    match = re.search(r"'dataset_fingerprint', '([0-9a-f]{64})'", seed_sql.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"no dataset fingerprint in {seed_sql}")
    return match.group(1)


def loaded_fingerprint() -> str | None:
    out = psql("SELECT value FROM seed_metadata WHERE key = 'dataset_fingerprint'").strip()
    return out or None
