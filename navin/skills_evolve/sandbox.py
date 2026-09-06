"""Isolated copy of the project skill layer for exams (S2.1).

The exam never reads the real ``.navin/skills`` while a draft is under test
and never writes there. ``ExamSandbox`` copies the project skill files
(``SKILL.md`` only, bounded size) into a temporary folder, adds or removes
the skill under test, and hands the resulting list of markdown texts to the
exam model. When the workspace is a git checkout the sandbox still only
needs the skill layer: the exam models answer prompts, they run no tools,
so a full worktree would be paid for nothing.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Iterator

from navin.skills_evolve.paths import SKILL_FILE
from navin.workspace_layout import skills_dir

_MAX_SKILL_BYTES = 256 * 1024
_MAX_SKILLS = 200


def project_skill_texts(workspace: Path | str, *, exclude: str | None = None) -> list[str]:
    """The project's own SKILL.md texts, alphabetical, ``exclude`` left out."""
    root = skills_dir(workspace)
    if not root.is_dir():
        return []
    texts: list[str] = []
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    for child in children[:_MAX_SKILLS]:
        if child.name == exclude:
            continue
        skill_file = child / SKILL_FILE if child.is_dir() else child
        if not skill_file.is_file() or skill_file.suffix.lower() != ".md":
            continue
        try:
            if skill_file.stat().st_size > _MAX_SKILL_BYTES:
                continue
            texts.append(skill_file.read_text(encoding="utf-8"))
        except OSError:
            continue
    return texts


class ExamSandbox:
    """A throwaway folder that mirrors the project skill layer."""

    def __init__(self, workspace: Path | str) -> None:
        self.workspace = Path(workspace)
        self.root: Path | None = None

    def __enter__(self) -> ExamSandbox:
        self.root = Path(tempfile.mkdtemp(prefix="navin-skills-exam-"))
        source = skills_dir(self.workspace)
        target = self.root / ".navin" / "skills"
        target.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            for child in source.iterdir():
                skill_file = child / SKILL_FILE
                if skill_file.is_file():
                    (target / child.name).mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(skill_file, target / child.name / SKILL_FILE)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.root is not None:
            shutil.rmtree(self.root, ignore_errors=True)
            self.root = None

    def _require_root(self) -> Path:
        if self.root is None:
            raise RuntimeError("sandbox is not open")
        return self.root

    def place(self, name: str, markdown: str) -> Path:
        """Put the skill under test in the sandbox copy (never in the project)."""
        root = self._require_root()
        folder = root / ".navin" / "skills" / name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / SKILL_FILE
        path.write_text(markdown, encoding="utf-8")
        return path

    def remove(self, name: str) -> None:
        root = self._require_root()
        shutil.rmtree(root / ".navin" / "skills" / name, ignore_errors=True)

    def skill_texts(self, *, exclude: str | None = None) -> list[str]:
        return project_skill_texts(self._require_root(), exclude=exclude)

    def iter_skill_names(self) -> Iterator[str]:
        root = self._require_root() / ".navin" / "skills"
        for child in sorted(root.iterdir(), key=lambda item: item.name):
            if (child / SKILL_FILE).is_file():
                yield child.name
