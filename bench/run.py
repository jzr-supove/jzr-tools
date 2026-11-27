"""Entry point: uv run python bench/run.py --help"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jzr_bench.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
