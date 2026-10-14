#!/usr/bin/env python3
"""Fail the build when main.kv calls something main.py does not provide.

Kivy wires the UI to Python at *runtime*, so a `.kv` file that calls a handler
which does not exist is perfectly legal at build time and dies on the first tap
on the phone. This bit us twice (`app.open_camera`, `app.copy_number`,
`app.search_history` were all referenced from `main.kv` and missing from the App
class), and no amount of building can reveal it - but cross-checking the two
files can, which is what this script does.

Checks, in order of how sure it is:

1. ``app.some_name(...)`` (or ``root.some_name(...)``) in the kv where no such
   method exists on the App class (resp. on a widget class): **error**. Calling
   something undefined always raises AttributeError, so this cannot be a false
   alarm - and on Android the exception closes the app.
2. ``self.ids.<name>`` in Python for an ``id: <name>`` the kv never declares:
   **error** (KeyError on first use).
3. Read-only references and ids nothing reads: reported as ``::notice`` only, so
   the check never blocks a build on a style question.

Usage: check_kv_wiring.py [main.py [main.kv]]
Exit status: 1 if an error was found, else 0.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

# What the Kivy App base class really provides, so `app.title` etc. stay quiet.
APP_BASE = {
    "title", "icon", "root", "running", "tint", "theme", "async_build", "use_kv",
    "graphics", "device", "window", "config", "builder", "get_running_app",
    "close", "stop", "run", "async_run", "pause", "resume", "open_kv",
    "load_string", "update_kv", "change_config", "exit",
}

CALL_RE = re.compile(r"\b(app|root)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
IDS_RE = re.compile(r"\bself\.ids\.([A-Za-z_][A-Za-z0-9_]*)")
KV_ID_RE = re.compile(r"^[ \t]*id:[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*$", re.M)


def _assigned_self_attrs(classdef: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(classdef):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    names.add(target.attr)
    return names


def collect(py_path: pathlib.Path) -> tuple[set[str], set[str]]:
    """(methods of App subclasses, methods of every other class in the file)."""
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    app_methods: set[str] = set()
    widget_methods: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {ast.unparse(base).split(".")[-1] for base in node.bases}
        is_app = "App" in bases or node.name.endswith("App")
        bucket = app_methods if is_app else widget_methods
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bucket.add(item.name)
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        bucket.add(target.id)
        # `self.foo = ...` inside the class body, e.g. handlers built in __init__
        bucket |= _assigned_self_attrs(node)
    return app_methods, widget_methods


def main(argv: list[str]) -> int:
    root_py = pathlib.Path(argv[1]) if len(argv) > 1 else pathlib.Path("main.py")
    kv = pathlib.Path(argv[2]) if len(argv) > 2 else root_py.with_suffix(".kv")
    if not root_py.is_file() or not kv.is_file():
        print(f"::warning title=KV wiring check skipped::no {root_py} / {kv} to check")
        return 0

    kv_text = kv.read_text(encoding="utf-8")
    app_methods, widget_methods = collect(root_py)
    allowed = {
        "app": app_methods | APP_BASE,
        "root": widget_methods | {"ids", "manager", "children", "parent"},
    }

    errors: list[str] = []
    notes: list[str] = []
    checked: set[tuple[str, str]] = set()

    for match in CALL_RE.finditer(kv_text):
        owner, name = match.group(1), match.group(2)
        if (owner, name) in checked:
            continue
        checked.add((owner, name))
        if name not in allowed[owner]:
            line = kv_text[:match.start()].count("\n") + 1
            target = f"def {name}" if owner == "app" else f"def {name} on a screen class"
            errors.append(
                f"{kv.name}:{line}: `{owner}.{name}()` is called from the .kv file "
                f"but there is no {target} in {root_py.name} - the tap raises "
                f"AttributeError and Android closes the app"
            )

    py_tree = ast.parse(py_text := root_py.read_text(encoding="utf-8"),
                        filename=str(root_py))
    used_ids = set(IDS_RE.findall(py_text))
    # `ids[widget_id]` / `ids["tess_result"]` are as real as `ids.tess_result`;
    # every string constant in the file counts as a possible id reference so the
    # "unused id" notice below can never fire on a subscripted lookup.
    referenced_by_string = {
        node.value for node in ast.walk(py_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    declared_ids = set(KV_ID_RE.findall(kv_text))

    for missing in sorted(used_ids - declared_ids):
        errors.append(
            f"{root_py.name}: `self.ids.{missing}` is read in Python, but "
            f"`{kv.name}` never declares `id: {missing}` -> KeyError the first "
            f"time that code runs"
        )
    for known_id in sorted(declared_ids - used_ids):
        # An id used by other kv rules is a deliberate handle, not dead weight.
        if known_id in referenced_by_string:
            continue
        if len(re.findall(rf"\b{re.escape(known_id)}\b", kv_text)) > 1:
            continue
        notes.append(f"`id: {known_id}` in {kv.name} is never read from anywhere")

    for line in notes:
        print("::notice::" + line)
    if not errors:
        print(f"kv wiring OK: {len(checked)} handler call(s) and "
              f"{len(used_ids)} id(s) resolved against {kv.name}")
        return 0

    for line in errors:
        print(line)
        print(f"::error::{line}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
