#!/usr/bin/env python3
"""Fail closed when Python source and release metadata disagree."""

from __future__ import annotations

import argparse
import ast
import re
import tomllib
from pathlib import Path


def literal_assignment(path: Path, name: str) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    matches: list[object] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            value = node.value
            if value is None:
                continue
            matches.append(ast.literal_eval(value))
    if len(matches) != 1:
        raise ValueError(f"{path} must define exactly one literal {name} assignment")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("version")
    parser.add_argument("edition")
    args = parser.parse_args()

    root = args.root.resolve()
    gui_path = root / "rfmapping_gui.py"
    pyproject_path = root / "pyproject.toml"
    requirements_path = root / "requirements.txt"
    for required in (gui_path, pyproject_path, requirements_path):
        if not required.is_file():
            raise FileNotFoundError(f"required release input is missing: {required}")

    source_version = literal_assignment(gui_path, "APP_VERSION")
    source_edition = literal_assignment(gui_path, "APP_EDITION")
    if source_version != args.version:
        raise ValueError(
            f"rfmapping_gui.py APP_VERSION is {source_version!r}; expected {args.version!r}"
        )
    if source_edition != args.edition:
        raise ValueError(
            f"rfmapping_gui.py APP_EDITION is {source_edition!r}; expected {args.edition!r}"
        )

    with pyproject_path.open("rb") as stream:
        project = tomllib.load(stream)["project"]
    if project.get("version") != args.version:
        raise ValueError(
            f"pyproject.toml version is {project.get('version')!r}; expected {args.version!r}"
        )
    dependencies = project.get("dependencies", [])
    if not any(re.fullmatch(r"tkinterdnd2==0\.6\.2", item) for item in dependencies):
        raise ValueError("pyproject.toml must pin tkinterdnd2==0.6.2")

    requirements = {
        line.strip()
        for line in requirements_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if "tkinterdnd2==0.6.2" not in requirements:
        raise ValueError("requirements.txt must pin tkinterdnd2==0.6.2")

    gui_source = gui_path.read_text(encoding="utf-8")
    if "--self-test-dnd" not in gui_source:
        raise ValueError("rfmapping_gui.py must expose the frozen TkDND smoke test")

    print(f"release metadata verified: Python {args.version} {args.edition}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
