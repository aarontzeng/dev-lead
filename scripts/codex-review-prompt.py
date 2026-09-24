#!/usr/bin/env python3
"""Compatibility name for `review-prompt.py --adapter codex` (0.6.28-0.6.29).

Usage: codex-review-prompt.py --base <rev> --target <frozen worktree> < lens > prompt

0.6.30 generalised the builder to every framed review leg; this name stays so
the commands already written down elsewhere keep working. `build` is re-exported
for the same reason.
"""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "review_prompt", Path(__file__).resolve().parent / "review-prompt.py")
_rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rp)
build = _rp.build

if __name__ == "__main__":
    _rp.main(["--adapter", "codex", *sys.argv[1:]], prog="codex-review-prompt")
