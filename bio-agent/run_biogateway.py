"""Standalone ingress relay using only the standard library."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from bioagent.gateway import main
if __name__ == "__main__":
    raise SystemExit(main())
