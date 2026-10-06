"""Run the mock Ollama (see hotel_evals/mock_ollama.py for what it is and its failure modes).

    python evals/mock_ollama.py --data data-gen/out --port 11500

Point n8n at it with:
    OLLAMA_BASE_URL=http://host.docker.internal:11500 bash scripts/setup-credentials.sh
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "data-gen"))  # hotel_datagen, for ABN formatting

from hotel_evals.mock_ollama import main  # noqa: E402

if __name__ == "__main__":
    main()
