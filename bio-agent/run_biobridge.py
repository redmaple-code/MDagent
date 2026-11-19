#!/usr/bin/env python3
"""Fixed stdio entry; selected environment only, no installation required."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from bioagent.bridge import main
if __name__ == "__main__":
    raise SystemExit(main())
