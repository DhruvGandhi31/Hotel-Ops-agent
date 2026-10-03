"""Generate the synthetic dataset.

    python data-gen/generate.py --n 200 --seed 42

Writes to data-gen/out/ (gitignored): invoices/*.pdf, ground_truth/*.json, manifest.json,
master_data.json, seed.sql and REPORT.md. Same (n, seed) always gives identical output.
"""

import argparse
import sys
from pathlib import Path

from hotel_datagen.build import build_dataset
from hotel_datagen.output import write_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=200, help="number of invoice files")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "out")
    args = parser.parse_args(argv)

    dataset = build_dataset(args.n, args.seed)
    manifest = write_dataset(dataset, args.out)
    print(f"wrote {len(manifest['files'])} invoices to {args.out}")
    print((args.out / "REPORT.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
