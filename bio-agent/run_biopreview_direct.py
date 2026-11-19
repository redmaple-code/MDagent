#!/usr/bin/env python3
"""Fixed source-only direct preview consumer entry."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from bioagent.visualization.direct_reader import main
if __name__ == '__main__': raise SystemExit(main())
