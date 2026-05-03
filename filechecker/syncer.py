"""Copy missing files with /bin/cp -p."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from .scanner import ErrorList, ProgressCallback, ScanResult, noop_progress
from .scanner import normalize_rel_path


@dataclass(frozen=True)
class CopyTask:
    direction: str
    rel_path: str
    source: Path
    destination: Path
    destination_rel_path: str
    size: int


@dataclass(frozen=True)
class CopyOutcome:
    copied: List[CopyTask]
    errors: ErrorList
    cancelled: bool


def rel_path_to_destination(root: Path, rel_path: str) -> Path:
    return root.joinpath(*rel_path.split("/"))


def unique_destination(root: Path, rel_path: str, reserved: set[str]) -> tuple[Path, str]:
    destination = rel_path_to_destination(root, rel_path)
    if _destination_is_available(destination, reserved):
        reserved.add(str(destination))
        return destination, rel_path

    parent = destination.parent
    stem = destination.stem
    suffix = destination.suffix
    counter = 1
    while True:
        label = "FileChecker copy" if counter == 1 else f"FileChecker copy {counter}"
        candidate = parent / f"{stem} ({label}){suffix}"
        if _destination_is_available(candidate, reserved):
            reserved.add(str(candidate))
            return candidate, normalize_rel_path(candidate, root)
        counter += 1


def _destination_is_available(destination: Path, reserved: set[str]) -> bool:
    return str(destination) not in reserved and not destination.exists()


def build_copy_tasks(result: ScanResult, root_a: Path, root_b: Path) -> List[CopyTask]:
    root_a = root_a.resolve()
    root_b = root_b.resolve()
    tasks: List[CopyTask] = []
    reserved_a: set[str] = set()
    reserved_b: set[str] = set()

    for record in result.to_copy_a_to_b:
        destination, destination_rel_path = unique_destination(
            root_b, record.rel_path, reserved_b
        )
        tasks.append(
            CopyTask(
                direction="A -> B",
                rel_path=record.rel_path,
                source=record.absolute_path,
                destination=destination,
                destination_rel_path=destination_rel_path,
                size=record.size,
            )
        )

    for record in result.to_copy_b_to_a:
        destination, destination_rel_path = unique_destination(
            root_a, record.rel_path, reserved_a
        )
        tasks.append(
            CopyTask(
                direction="B -> A",
                rel_path=record.rel_path,
                source=record.absolute_path,
                destination=destination,
                destination_rel_path=destination_rel_path,
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
            errors.append((_task_label(task), str(exc)))
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
            errors.append((_task_label(task), message))
        else:
            copied.append(task)

        progress(f"Copied {index}/{total}", index, total)

    return CopyOutcome(
        copied=copied,
        errors=errors,
        cancelled=cancel_event.is_set(),
    )


def _task_label(task: CopyTask) -> str:
    if task.rel_path == task.destination_rel_path:
        return task.rel_path
    return f"{task.rel_path} -> {task.destination_rel_path}"
