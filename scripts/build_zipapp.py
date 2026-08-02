from __future__ import annotations

import argparse
import zipapp
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the portable SERA zipapp.")
    parser.add_argument("--output", type=Path, default=Path("dist/sera.pyz"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    zipapp.create_archive(
        root / "src",
        target=args.output,
        main="sera.cli:main",
        interpreter="/usr/bin/env python3",
        compressed=True,
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
