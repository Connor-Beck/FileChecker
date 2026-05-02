"""Copy missing files with /bin/cp -p."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from .scanner import ErrorList, ScanResult, noop_progress, ProgressCallback


@dataclass(frozen=True)
class CopyTask:
    direction: str
    rel_path: str
    source: Path
    destination: Path
    size: int


@dataclass(frozen=True)
class CopyOutcome:
    copied: List[CopyTask]
    errors: ErrorList
    cancelled: bool


def rel_path_to_destination(root: Path, rel_path: str) -> Path:
    return root.joinpath(*rel_path.split("/"))


def build_copy_tasks(result: ScanResult, root_a: Path, root_b: Path) -> List[CopyTask]:
    root_a = root_a.resolve()
    root_b = root_b.resolve()
    tasks: List[CopyTask] = []

    for record in result.to_copy_a_to_b:
        tasks.append(
            CopyTask(
                direction="A -> B",
                rel_path=record.rel_path,
                source=record.absolute_path,
                destination=rel_path_to_destination(root_b, record.rel_path),
                size=record.size,
            )
        )

    for record in result.to_copy_b_to_a:
        tasks.append(
            CopyTask(
                direction="B -> A",
                rel_path=record.rel_path,
                source=record.absolute_path,
                destination=rel_path_to_destination(root_a, record.rel_path),
                size=record.size,
            )
        )

    return tasks


def copy_tasks(
    tasks: Sequence[CopyTask],
    cancel_event: threading.Event,
    progress: ProgressCallback = noop_progress,
) -> CopyOutcome:
    copied: List[CopyTask] = []
    errors: ErrorList = []
    total = len(tasks)

    for index, task in enumerate(tasks, start=1):
        if cancel_event.is_set():
            return CopyOutcome(copied=copied, errors=errors, cancelled=True)

        progress(f"Copying {index}/{total}: {task.rel_path}", index - 1, total)

        try:
            task.destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            errors.append((task.rel_path, str(exc)))
            progress(f"Skipped {task.rel_path}", index, total)
            continue

        result = subprocess.run(
            ["/bin/cp", "-p", str(task.source), str(task.destination)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            if not message:
                message = f"/bin/cp exited with status {result.returncode}"
            errors.append((task.rel_path, message))
        else:
            copied.append(task)

        progress(f"Copied {index}/{total}", index, total)

    return CopyOutcome(
        copied=copied,
        errors=errors,
        cancelled=cancel_event.is_set(),
    )
