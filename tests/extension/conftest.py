import sys
from pathlib import Path

# The extension's tests share the pane's kit: the same thurbox-cli stand-in,
# layout fixture and throwaway checkout, whether or not tests/pane is collected.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pane"))
