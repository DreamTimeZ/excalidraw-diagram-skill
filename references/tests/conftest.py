import sys
from pathlib import Path

# Make render_excalidraw.py (one level up) importable by the tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
