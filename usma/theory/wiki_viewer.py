"""Lightweight in-app theory wiki viewer.

Exposes:
    - :class:`TheoryWikiViewer` — a ``tk.Toplevel`` popup that renders a single
      pseudo-markdown ``.txt`` file from ``usma/theory/pages/``.
    - :func:`show_theory_page` — the public entry point, enforces one-instance
      -per-page so repeated clicks on the same ⓘ raise the existing window.
    - :func:`make_info_button` — DRY helper that builds a standardised ⓘ label
      and wires its ``<Button-1>`` handler.

The viewer is pure Tkinter — no additional dependencies.
"""

from __future__ import annotations

import logging
import os
import re
import tkinter as tk
from tkinter import ttk
from typing import Dict

logger = logging.getLogger(__name__)

# Module-level registry of currently-open windows, keyed by page_id. Used to
# ensure that clicking the same ⓘ twice raises the existing window instead of
# spawning duplicates.
_open_windows: Dict[str, "TheoryWikiViewer"] = {}

PAGES_DIR = os.path.join(os.path.dirname(__file__), "pages")


def show_theory_page(parent: tk.Misc, page_id: str) -> "TheoryWikiViewer":
    """Open (or bring to front) the theory page for the given ``page_id``.

    This is the public API. Call from any widget::

        from usma.theory.wiki_viewer import show_theory_page
        show_theory_page(self.root, "fft_cutoff_frequency")

    If a window for ``page_id`` is already open, it is deiconified, raised and
    focused — no duplicate is created. If the existing window has been
    destroyed externally the registry entry is cleaned up and a fresh window
    is spawned.
    """
    existing = _open_windows.get(page_id)
    if existing is not None:
        try:
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return existing
        except tk.TclError:
            # Window was destroyed without our _on_close firing
            _open_windows.pop(page_id, None)

    viewer = TheoryWikiViewer(parent, page_id)
    _open_windows[page_id] = viewer
    return viewer


def make_info_button(parent: tk.Misc, root: tk.Misc, page_id: str,
                     **pack_kwargs) -> tk.Label:
    """Create a clickable ⓘ label that opens the corresponding theory page.

    Usage::

        from usma.theory.wiki_viewer import show_theory_page, make_info_button
        make_info_button(row_frame, self.root, "fft_cutoff_frequency",
                         side=tk.LEFT, padx=2)

    Args:
        parent: The frame to place the label in.
        root: The root Tk window (passed to ``TheoryWikiViewer`` as parent).
        page_id: Identifier matching a ``.txt`` file in ``usma/theory/pages/``.
        **pack_kwargs: Passed through to ``label.pack()``.

    Returns:
        The created ``tk.Label`` widget so the caller can further configure it
        (e.g. setting ``bg`` to match a coloured banner).
    """
    label = tk.Label(
        parent, text="\u24D8", font=("Segoe UI", 10), fg="#5DADE2",
        cursor="hand2", padx=2, borderwidth=0,
    )
    label.bind("<Button-1>", lambda _e: show_theory_page(root, page_id))
    if pack_kwargs:
        label.pack(**pack_kwargs)
    return label


class TheoryWikiViewer(tk.Toplevel):
    """Read-only scrolled-text popup displaying a single theory page."""

    BG = "#2E2E2E"
    FG = "#EEEEEE"
    HEADING_FG = "#5DADE2"
    CODE_BG = "#1E1E1E"
    CODE_FG = "#A9DFBF"
    TABLE_FG = "#D5DBDB"

    def __init__(self, parent: tk.Misc, page_id: str):
        super().__init__(parent)
        self.page_id = page_id
        pretty = page_id.replace("_", " ").title()
        self.title(f"USMA Theory \u2014 {pretty}")
        self.geometry("700x500")
        self.minsize(480, 320)
        self.configure(bg=self.BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # --- Scrolled text area ---
        frame = tk.Frame(self, bg=self.BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.text = tk.Text(
            frame, wrap=tk.WORD, bg=self.BG, fg=self.FG,
            font=("Segoe UI", 10), relief=tk.FLAT,
            yscrollcommand=scrollbar.set, state=tk.DISABLED,
            padx=12, pady=8, spacing1=2, spacing3=2,
            insertbackground=self.FG,
        )
        self.text.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.text.yview)

        # --- Text tags ---
        self.text.tag_configure("h1", font=("Segoe UI", 14, "bold"),
                                foreground=self.HEADING_FG,
                                spacing1=12, spacing3=6)
        self.text.tag_configure("h2", font=("Segoe UI", 12, "bold"),
                                foreground=self.HEADING_FG,
                                spacing1=10, spacing3=4)
        self.text.tag_configure("h3", font=("Segoe UI", 10, "bold"),
                                foreground=self.HEADING_FG,
                                spacing1=6, spacing3=2)
        self.text.tag_configure("code", font=("Consolas", 9),
                                background=self.CODE_BG, foreground=self.CODE_FG,
                                spacing1=2, spacing3=2,
                                lmargin1=20, lmargin2=20)
        self.text.tag_configure("table", font=("Consolas", 9),
                                foreground=self.TABLE_FG,
                                lmargin1=10, lmargin2=10)
        self.text.tag_configure("bold", font=("Segoe UI", 10, "bold"))
        self.text.tag_configure("normal", font=("Segoe UI", 10))
        self.text.tag_configure("bullet", lmargin1=20, lmargin2=32)
        self.text.tag_configure("link_hint", foreground="#85C1E9",
                                font=("Segoe UI", 9, "italic"), spacing1=8)

        # --- Load content ---
        self._load_page(page_id)

    # ------------------------------------------------------------------ loaders
    def _load_page(self, page_id: str):
        """Load and render a ``.txt`` file from the pages directory."""
        filepath = os.path.join(PAGES_DIR, f"{page_id}.txt")
        if not os.path.isfile(filepath):
            msg = (f"Page not found: {page_id}\n\n"
                   f"Expected file: {filepath}")
            self._insert_text(msg, "normal")
            logger.warning("[THEORY] Page file not found: %s", filepath)
            return

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:  # noqa: BLE001 - surface any read error to user
            self._insert_text(f"Error loading page: {e}", "normal")
            logger.exception("[THEORY] Failed to read %s", filepath)
            return

        self._render_content(content)

    # ------------------------------------------------------------------ rendering
    def _render_content(self, content: str):
        """Parse pseudo-markdown content and insert with tags."""
        self.text.config(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)

        in_code_block = False
        lines = content.split("\n")
        for line in lines:
            stripped = line.strip()

            # Code-block fence toggle
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue

            if in_code_block:
                self.text.insert(tk.END, line + "\n", "code")
                continue

            # Headings
            if line.startswith("### "):
                self.text.insert(tk.END, line[4:] + "\n", "h3")
            elif line.startswith("## "):
                self.text.insert(tk.END, line[3:] + "\n", "h2")
            elif line.startswith("# "):
                self.text.insert(tk.END, line[2:] + "\n", "h1")
            # Table rows (pipe-delimited)
            elif stripped.startswith("|"):
                self.text.insert(tk.END, line + "\n", "table")
            # Bullet points
            elif stripped.startswith("- "):
                self._insert_rich_line("  \u2022 " + stripped[2:] + "\n", "bullet")
            # Horizontal rule
            elif stripped == "---":
                self.text.insert(tk.END, "\u2500" * 60 + "\n", "normal")
            # "See also:" footer — render as italic hint
            elif stripped.lower().startswith("see also:"):
                self.text.insert(tk.END, line + "\n", "link_hint")
            # Empty line
            elif stripped == "":
                self.text.insert(tk.END, "\n")
            # Normal text (with inline bold support)
            else:
                self._insert_rich_line(line + "\n", "normal")

        self.text.config(state=tk.DISABLED)
        self.text.yview_moveto(0.0)

    def _insert_rich_line(self, line: str, base_tag: str):
        """Insert a line, rendering ``**bold**`` inline markers as bold."""
        parts = re.split(r"(\*\*.*?\*\*)", line)
        for part in parts:
            if part.startswith("**") and part.endswith("**") and len(part) > 4:
                self.text.insert(tk.END, part[2:-2], "bold")
            else:
                self.text.insert(tk.END, part, base_tag)

    def _insert_text(self, text: str, tag: str):
        """Helper to insert text while respecting the read-only state."""
        self.text.config(state=tk.NORMAL)
        self.text.insert(tk.END, text, tag)
        self.text.config(state=tk.DISABLED)

    # ------------------------------------------------------------------ lifecycle
    def _on_close(self):
        """Clean up registry on window close."""
        _open_windows.pop(self.page_id, None)
        self.destroy()
