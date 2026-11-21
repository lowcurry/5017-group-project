"""Replay the frozen study from an extracted submission directory."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from curve_audit.formal_replay import main


if __name__ == '__main__':
    main(ROOT)
