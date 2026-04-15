"""USMA theory wiki package — in-app contextual help.

Re-exports the public API of :mod:`usma.theory.wiki_viewer` so callers can do
``from usma.theory import show_theory_page, make_info_button`` without needing
to reach into the submodule.
"""

from usma.theory.wiki_viewer import show_theory_page, make_info_button

__all__ = ["show_theory_page", "make_info_button"]
