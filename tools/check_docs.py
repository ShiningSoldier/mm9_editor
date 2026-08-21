"""Validate portable, internally consistent repository documentation."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List
from urllib.parse import unquote


WINDOWS_ABSOLUTE_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]")
USER_ABSOLUTE_RE = re.compile(r"/(?:home|Users)/[^\s`)>]+")
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
REPO_REFERENCE_RE = re.compile(
    r"`((?:app|ui|core|catalog|features|conversion|view3d|mm9_patcher|"
    r"tests|tools|docs)/[^`\s]+)`"
)

ALLOWED_ROOT_DOCS = {"README.md"}
REQUIRED_DOCS = {
    "docs/README.md",
    "docs/user-guide/editor-workflow.md",
    "docs/user-guide/viewport.md",
    "docs/user-guide/prefab-import.md",
    "docs/user-guide/dialogue-and-quests.md",
    "docs/user-guide/model-export.md",
    "docs/user-guide/conversions/lomm-to-mm9.md",
    "docs/user-guide/conversions/gltf-to-ed.md",
    "docs/user-guide/conversions/dat-to-ed.md",
    "docs/reference/game-resources.md",
    "docs/reference/world-data.md",
    "docs/reference/rude-format.md",
    "docs/reference/conversion-contracts/gltf-to-ed.md",
    "docs/reference/conversion-contracts/dat-to-ed.md",
    "docs/development/architecture.md",
    "docs/development/testing.md",
    "docs/development/release-validation.md",
    "docs/research/README.md",
}


def documentation_files(root: Path) -> Iterable[Path]:
    yield root / "README.md"
    yield from sorted((root / "docs").rglob("*.md"))


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _link_path(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    # The repository docs use simple local targets without quoted link titles.
    # Retain spaces in filenames instead of trying to parse arbitrary Markdown.
    return unquote(target.split("#", 1)[0])


def check_repository(root: Path) -> List[str]:
    root = root.resolve()
    issues: List[str] = []

    docs_root = root / "docs"
    if not (root / "README.md").is_file():
        issues.append("README.md: missing repository entry point")
    if not docs_root.is_dir():
        issues.append("docs: missing documentation directory")
        return issues

    root_docs = {path.name for path in docs_root.glob("*.md")}
    unexpected = sorted(root_docs - ALLOWED_ROOT_DOCS)
    if unexpected:
        issues.append(
            "docs: legacy root-level documents remain: " + ", ".join(unexpected)
        )

    for required in sorted(REQUIRED_DOCS):
        if not (root / required).is_file():
            issues.append(f"{required}: required document is missing")

    for path in documentation_files(root):
        if not path.is_file():
            continue
        rel = _relative(root, path)
        text = path.read_text(encoding="utf-8")

        for line_number, line in enumerate(text.splitlines(), 1):
            if WINDOWS_ABSOLUTE_RE.search(line) or USER_ABSOLUTE_RE.search(line):
                issues.append(
                    f"{rel}:{line_number}: workstation-specific absolute path"
                )

        for match in MARKDOWN_LINK_RE.finditer(text):
            raw_target = match.group(1).strip()
            lower = raw_target.lower()
            if (
                not raw_target
                or raw_target.startswith("#")
                or lower.startswith(("http://", "https://", "mailto:"))
            ):
                continue
            local = _link_path(raw_target)
            if not local:
                continue
            resolved = (path.parent / local).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                issues.append(f"{rel}: link escapes repository: {raw_target}")
                continue
            if not resolved.exists():
                issues.append(f"{rel}: broken local link: {raw_target}")

        for match in REPO_REFERENCE_RE.finditer(text):
            token = match.group(1).rstrip(".,;:")
            candidate = root / token
            if not candidate.exists():
                issues.append(f"{rel}: missing repository reference: {token}")

    return sorted(set(issues))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    issues = check_repository(root)
    if issues:
        print("Documentation audit failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    count = sum(1 for _ in documentation_files(root))
    print(f"Documentation audit passed ({count} Markdown files checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

