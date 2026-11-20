"""Paired desktop colors, in CustomTkinter's (light, dark) order."""
from __future__ import annotations

import os
from pathlib import Path

BG = ("#EAF0FD", "#0B0921")
RAIL = ("#F7FAFF", "#100D2A")
PANEL = ("#FBFCFF", "#171335")
INSET = ("#EDF2FF", "#12102B")
HOVER = ("#DFE7FF", "#28204E")
LINE = ("#B9C7EC", "#3C3263")
TEXT = ("#151C44", "#F2EEFF")
MUTED = ("#4E5B80", "#ADA6CE")
ACCENT = ("#4442C3", "#63F0EB")
ACCENT_HOVER = ("#3835A8", "#A3FAF5")
ACTION_TEXT = ("#FFFFFF", "#111029")
FOCUS = ("#453BC5", "#9CFCF8")
WARN = ("#8D4200", "#FFD39C")
WARN_LINE = ("#C69C75", "#88613E")
GOOD = ("#076857", "#7BEBC9")
VIOLET = ("#5F3DAD", "#C7ABFF")
HERO = ("#E2EAFF", "#171440")
BRAND = ("#232457", "#242052")

THEME_LABELS = {"Light": "白昼", "Dark": "黑夜"}


def preference_path() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    directory = Path(root) if root else Path.home() / ".config"
    return directory / "JumpServer" / "WatchdogConsole" / "appearance.txt"


def load_appearance(path: Path | None = None) -> str:
    try:
        value = (path or preference_path()).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return "Dark"
    return value if value in THEME_LABELS else "Dark"


def save_appearance(value: str, path: Path | None = None) -> bool:
    destination = path or preference_path()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(value + "\n", encoding="utf-8")
    except OSError:
        return False
    return True
