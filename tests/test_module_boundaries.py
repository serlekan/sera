"""Enforce the one-way domain-module dependency architecture."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "sera"
DOMAIN_MODULES = {
    "schemas",
    "provenance",
    "packets",
    "verification",
    "knowledge",
    "assurance",
    "reporting",
}


def imported_sera_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("sera."):
                    modules.add(alias.name.split(".", 1)[1].split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.module:
                modules.add(node.module.split(".", 1)[0])
            elif node.module and node.module.startswith("sera."):
                modules.add(node.module.split(".", 1)[1].split(".", 1)[0])
    return modules


class ModuleBoundaryTests(unittest.TestCase):
    def test_schemas_is_stdlib_only(self) -> None:
        path = SOURCE_ROOT / "schemas.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.ImportFrom) and node.level:
                self.fail(f"schemas.py imports local module {node.module!r}")
        self.assertLessEqual(imports, sys.stdlib_module_names)

    def test_domain_modules_do_not_import_facades(self) -> None:
        for name in DOMAIN_MODULES:
            path = SOURCE_ROOT / f"{name}.py"
            if not path.exists():
                continue
            with self.subTest(module=name):
                self.assertTrue(imported_sera_modules(path).isdisjoint({"cli", "controller"}))

    def test_lower_domain_modules_do_not_import_higher_authorities(self) -> None:
        forbidden = {
            "schemas": DOMAIN_MODULES - {"schemas"},
            "provenance": {"packets", "assurance", "reporting"},
            "packets": {"assurance", "reporting"},
            "verification": {"assurance", "reporting"},
            "knowledge": {"assurance", "reporting"},
            "assurance": {"reporting"},
        }
        for name, disallowed in forbidden.items():
            path = SOURCE_ROOT / f"{name}.py"
            if not path.exists():
                continue
            with self.subTest(module=name):
                self.assertTrue(imported_sera_modules(path).isdisjoint(disallowed))


if __name__ == "__main__":
    unittest.main()
