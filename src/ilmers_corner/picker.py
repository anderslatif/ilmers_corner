"""Interactive arrow-key selection list.

Written against the terminal directly rather than a TUI dependency so that the
package installs with nothing but the standard library. Falls back to numbered
input when stdin is not a TTY.
"""

from __future__ import annotations

import sys
from typing import Callable, Optional, Sequence, TypeVar

from .terminal import (
    CLEAR_LINE,
    CURSOR_UP,
    dim,
    hide_cursor,
    is_interactive,
    raw_mode,
    read_key,
    show_cursor,
    style,
)

Item = TypeVar("Item")

PAGE_SIZE = 12


def select(
    items: Sequence[Item],
    render: Callable[[Item, bool], str],
    *,
    title: str,
    on_page_change: Optional[Callable[[Sequence[Item]], None]] = None,
    footer: str = "",
) -> Optional[Item]:
    """Show a scrolling list and return the chosen item, or None if cancelled.

    `on_page_change` is invoked with the currently visible slice before each
    repaint, which is how commit dates get fetched lazily for just those rows.
    """
    if not items:
        return None
    if not is_interactive():
        return _select_without_tty(items, render, title=title, on_page_change=on_page_change)

    index = 0
    window_start = 0
    page = min(PAGE_SIZE, len(items))
    previous_line_count = 0

    stream = sys.stderr
    hide_cursor(stream)
    try:
        with raw_mode():
            while True:
                if index < window_start:
                    window_start = index
                elif index >= window_start + page:
                    window_start = index - page + 1

                visible = items[window_start : window_start + page]
                if on_page_change:
                    on_page_change(visible)

                lines = _compose(
                    items, visible, index, window_start, render, title=title, footer=footer
                )
                _repaint(stream, lines, previous_line_count)
                previous_line_count = len(lines)

                key = read_key()
                if key in ("up", "k"):
                    index = (index - 1) % len(items)
                elif key in ("down", "j"):
                    index = (index + 1) % len(items)
                elif key == "pageup":
                    index = max(0, index - page)
                elif key == "pagedown":
                    index = min(len(items) - 1, index + page)
                elif key == "home":
                    index = 0
                elif key == "end":
                    index = len(items) - 1
                elif key == "enter":
                    _erase(stream, previous_line_count)
                    return items[index]
                elif key in ("escape", "ctrl-c", "q"):
                    _erase(stream, previous_line_count)
                    return None
    finally:
        show_cursor(stream)


def _compose(
    items: Sequence[Item],
    visible: Sequence[Item],
    index: int,
    window_start: int,
    render: Callable[[Item, bool], str],
    *,
    title: str,
    footer: str,
) -> list[str]:
    lines = [title]
    for offset, item in enumerate(visible):
        absolute = window_start + offset
        selected = absolute == index
        pointer = style("❯ ", "cyan", bold=True) if selected else "  "
        lines.append(f"{pointer}{render(item, selected)}")

    hint = footer or "↑/↓ move · enter select · q cancel"
    if len(items) > len(visible):
        hint = f"{hint} · {index + 1}/{len(items)}"
    lines.append(dim(f"  {hint}"))
    return lines


def _repaint(stream, lines: list[str], previous_line_count: int) -> None:
    if previous_line_count:
        stream.write(CURSOR_UP * previous_line_count)
    for line in lines:
        stream.write(f"\r{CLEAR_LINE}{line}\r\n")
    # Clear any rows left behind by a shorter repaint.
    for _ in range(max(0, previous_line_count - len(lines))):
        stream.write(f"\r{CLEAR_LINE}\r\n")
    if previous_line_count > len(lines):
        stream.write(CURSOR_UP * (previous_line_count - len(lines)))
    stream.flush()


def _erase(stream, line_count: int) -> None:
    if line_count:
        stream.write(CURSOR_UP * line_count)
        for _ in range(line_count):
            stream.write(f"\r{CLEAR_LINE}\r\n")
        stream.write(CURSOR_UP * line_count)
    stream.flush()


def _select_without_tty(
    items: Sequence[Item],
    render: Callable[[Item, bool], str],
    *,
    title: str,
    on_page_change: Optional[Callable[[Sequence[Item]], None]] = None,
) -> Optional[Item]:
    """Numbered fallback for pipes and CI, where raw mode is unavailable."""
    shown = items[:PAGE_SIZE]
    if on_page_change:
        on_page_change(shown)

    print(title, file=sys.stderr)
    for position, item in enumerate(shown, start=1):
        print(f"  {position:>2}. {render(item, False)}", file=sys.stderr)

    # input()'s own prompt goes to stdout, which would contaminate the `uses:`
    # line when the command is piped; write it to stderr instead.
    sys.stderr.write("Select a number (blank to cancel): ")
    sys.stderr.flush()
    try:
        answer = sys.stdin.readline().strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not answer:
        return None
    if not answer:
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(shown):
        return shown[int(answer) - 1]
    print("Not a valid selection.", file=sys.stderr)
    return None
