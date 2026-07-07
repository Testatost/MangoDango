from __future__ import annotations

import py_compile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFLICT_MARKERS = ("<" * 7, "=" * 7, ">" * 7)


class SourceIntegrityTests(unittest.TestCase):
    def test_no_unresolved_merge_conflict_markers(self) -> None:
        offenders: list[str] = []
        for path in ROOT.rglob("*"):
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if any(marker in text for marker in CONFLICT_MARKERS):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual([], offenders)

    def test_main_window_compiles(self) -> None:
        py_compile.compile(str(ROOT / "mangodango" / "main_window.py"), doraise=True)


if __name__ == "__main__":
    unittest.main()
