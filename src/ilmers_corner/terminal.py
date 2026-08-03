"""Terminal styling and raw-mode key reading."""

from __future__ import annotations

import contextlib
import os
import sys
from typing import Iterator

CURSOR_UP = "\x1b[A"
CLEAR_LINE = "\x1b[2K"

COLORS = {
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
    "cyan": "\x1b[36m",
    "grey": "\x1b[90m",
}
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"


def supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stderr.isatty() and os.environ.get("TERM") != "dumb"


def style(text: str, color: str = "", *, bold: bool = False) -> str:
    if not supports_color():
        return text
    prefix = ""
    if bold:
        prefix += BOLD
    if color in COLORS:
        prefix += COLORS[color]
    return f"{prefix}{text}{RESET}" if prefix else text


def dim(text: str) -> str:
    return f"{DIM}{text}{RESET}" if supports_color() else text


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def hide_cursor(stream) -> None:
    if supports_color():
        stream.write("\x1b[?25l")
        stream.flush()


def show_cursor(stream) -> None:
    if supports_color():
        stream.write("\x1b[?25h")
        stream.flush()


@contextlib.contextmanager
def raw_mode() -> Iterator[None]:
    """Put the terminal in cbreak mode so single keypresses arrive at once."""
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    saved = termios.tcgetattr(descriptor)
    try:
        tty.setcbreak(descriptor)
        yield
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, saved)


def read_key() -> str:
    """Read one keypress, decoding the common escape sequences."""
    first = sys.stdin.read(1)
    if first == "\x03":
        return "ctrl-c"
    if first in ("\r", "\n"):
        return "enter"
    if first == "\x1b":
        second = sys.stdin.read(1)
        if second != "[":
            return "escape"
        third = sys.stdin.read(1)
        simple = {"A": "up", "B": "down", "C": "right", "D": "left",
                  "H": "home", "F": "end"}
        if third in simple:
            return simple[third]
        if third.isdigit():
            # Sequences like ESC[5~ (page up) carry a trailing tilde.
            tail = ""
            while True:
                character = sys.stdin.read(1)
                if character == "~" or not character:
                    break
                tail += character
            numeric = {"5": "pageup", "6": "pagedown", "1": "home", "4": "end"}
            return numeric.get(third, "unknown")
        return "unknown"
    return first.lower()
