#!/usr/bin/env python3
"""Owned compute-side preview entry, no installation or shell commands."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from bioagent.visualization.worker import main
if __name__=='__main__':raise SystemExit(main())
