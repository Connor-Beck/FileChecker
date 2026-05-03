"""Copy, replace, and trash files after a dry-run preview."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

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
    overwrite: bool = False
    action: str = "Copy"


@dataclass(frozen=True)
class DeleteTask:
    rel_path: str
    path: Path
    size: int


@dataclass(frozen=True)
class CopyOutcome:
    copied: List[CopyTask]
    errors: ErrorList
    cancelled: bool


@dataclass(frozen=True)
class DeleteOutcome:
    deleted: List[DeleteTask]
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

    for record in result.to_replace_a_to_b:
        tasks.append(
            CopyTask(
                direction="A -> B",
                rel_path=record.rel_path,
                source=record.absolute_path,
                destination=rel_path_to_destination(root_b, record.rel_path),
                destination_rel_path=record.rel_path,
                size=record.size,
                overwrite=True,
                action="Replace",
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


def build_delete_tasks(result: ScanResult) -> List[DeleteTask]:
    return [
        DeleteTask(
            rel_path=record.rel_path,
            path=record.absolute_path,
            size=record.size,
        )
        for record in result.to_delete_b
    ]


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

        progress(f"{task.action} {index}/{total}: {task.rel_path}", index - 1, total)

        try:
            task.destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            errors.append((_task_label(task), str(exc)))
            progress(f"Skipped {task.rel_path}", index, total)
            continue

        if not task.overwrite and task.destination.exists():
            errors.append((_task_label(task), "Destination already exists"))
            progress(f"Skipped {task.rel_path}", index, total)
            continue

        error = _copy_file_preserving_metadata(task.source, task.destination)
        if error:
            errors.append((_task_label(task), error))
        else:
            copied.append(task)

        done_label = "Copied" if task.action == "Copy" else "Replaced"
        progress(f"{done_label} {index}/{total}", index, total)

    return CopyOutcome(
        copied=copied,
        errors=errors,
        cancelled=cancel_event.is_set(),
    )


def delete_tasks(
    tasks: Sequence[DeleteTask],
    cancel_event: threading.Event,
    progress: ProgressCallback = noop_progress,
) -> DeleteOutcome:
    deleted: List[DeleteTask] = []
    errors: ErrorList = []
    total = len(tasks)

    for index, task in enumerate(tasks, start=1):
        if cancel_event.is_set():
            return DeleteOutcome(deleted=deleted, errors=errors, cancelled=True)

        progress(f"Deleting {index}/{total}: {task.rel_path}", index - 1, total)
        try:
            move_file_to_trash(task.path)
        except OSError as exc:
            errors.append((task.rel_path, str(exc)))
        else:
            deleted.append(task)
        progress(f"Deleted {index}/{total}", index, total)

    return DeleteOutcome(
        deleted=deleted,
        errors=errors,
        cancelled=cancel_event.is_set(),
    )


def _copy_file_preserving_metadata(source: Path, destination: Path) -> Optional[str]:
    if _can_use_posix_cp():
        result = subprocess.run(
            ["/bin/cp", "-p", str(source), str(destination)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            if not message:
                message = f"/bin/cp exited with status {result.returncode}"
            return message
        return None

    try:
        shutil.copy2(source, destination)
    except OSError as exc:
        return str(exc)
    return None


def _can_use_posix_cp() -> bool:
    return os.name != "nt" and Path("/bin/cp").exists()


def move_file_to_trash(path: Path) -> Path:
    path = path.resolve()
    if not path.exists():
        raise OSError(f"File not found: {path}")

    try:
        from send2trash import send2trash  # type: ignore
    except ImportError:
        return _move_file_to_platform_trash(path)

    try:
        send2trash(str(path))
    except Exception as exc:
        raise OSError(str(exc)) from exc
    return path


def _move_file_to_platform_trash(path: Path) -> Path:
    if os.name == "nt":
        return _move_file_to_windows_recycle_bin(path)

    if os.name == "posix" and Path.home().joinpath(".Trash").exists():
        trash = Path.home() / ".Trash"
    else:
        trash = Path.home() / ".local" / "share" / "Trash" / "files"
    trash.mkdir(parents=True, exist_ok=True)
    destination = _unique_trash_destination(trash, path.name)
    shutil.move(str(path), str(destination))
    return destination


def _move_file_to_windows_recycle_bin(path: Path) -> Path:
    import ctypes
    from ctypes import wintypes

    file_list = f"{path}\0\0"

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    fo_delete = 3
    fof_allowundo = 0x0040
    fof_noconfirmation = 0x0010
    fof_noerrorui = 0x0400
    operation = SHFILEOPSTRUCTW(
        None,
        fo_delete,
        file_list,
        None,
        fof_allowundo | fof_noconfirmation | fof_noerrorui,
        False,
        None,
        None,
    )
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0:
        raise OSError(f"Windows recycle bin operation failed with code {result}")
    if operation.fAnyOperationsAborted:
        raise OSError("Windows recycle bin operation was cancelled")
    return path


def _unique_trash_destination(trash: Path, name: str) -> Path:
    destination = trash / name
    if not destination.exists():
        return destination

    path = Path(name)
    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        label = "FileChecker deleted" if counter == 1 else f"FileChecker deleted {counter}"
        candidate = trash / f"{stem} ({label}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _task_label(task: CopyTask) -> str:
    if task.rel_path == task.destination_rel_path:
        return task.rel_path
    return f"{task.rel_path} -> {task.destination_rel_path}"
