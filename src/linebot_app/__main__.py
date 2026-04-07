from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    # Support running `python src/linebot_app/__main__.py` directly.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from linebot_app import main
else:
    from . import main

if __name__ == "__main__":
    main()
