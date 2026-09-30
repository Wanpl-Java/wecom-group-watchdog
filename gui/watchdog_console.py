"""PyInstaller entry: WeCom Watchdog desktop console."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepare_paths() -> None:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
        app_dir = base / "app"
        # Bundled Tcl/Tk (PyInstaller usually includes these under _MEIPASS)
        for tcl_name, env in (("tcl8.6", "TCL_LIBRARY"), ("tk8.6", "TK_LIBRARY")):
            for cand in (base / "tcl" / tcl_name, base / tcl_name):
                marker = "init.tcl" if "tcl" in tcl_name else "tk.tcl"
                if (cand / marker).is_file():
                    os.environ.setdefault(env, str(cand))
                    break
    else:
        app_dir = Path(__file__).resolve().parent / "app"
    if app_dir.is_dir() and str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))


_prepare_paths()

from main import main  # noqa: E402


if __name__ == "__main__":
    main()
