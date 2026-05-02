"""Recursive folder scanning and comparison logic."""

from __future__ import annotations

import os
import stat
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .constants import IGNORED_NAMES, IGNORED_PREFIXES, PROGRESS_EVERY_FILES
from .constants import SIZE_DIFF_THRESHOLD

ProgressCallback = Callable[[str, Optional[int], Optional[int]], None]
ErrorList = List[Tuple[str, str]]


class CancelledError(Exception):
    """Raised inside worker functions when the user requests cancellation."""


@dataclass(frozen=True)
class FileRecord:
    rel_path: str
    absolute_path: Path
    size: int


@dataclass(frozen=True)
class SizeMismatch:
    rel_path: str
    size_a: int
    size_b: int
    fraction: float


@dataclass(frozen=True)
class ScanResult:
    to_copy_a_to_b: List[FileRecord]
    to_copy_b_to_a: List[FileRecord]
    size_mismatches: List[SizeMismatch]
    errors: ErrorList


def noop_progress(message: str, current: Optional[int], total: Optional[int]) -> None:
    del message, current, total


def is_ignored_name(name: str) -> bool:
    return name in IGNORED_NAMES or name.startswith(IGNORED_PREFIXES)


def normalize_rel_path(path: Path, root: Path) -> str:
    rel = os.path.relpath(str(path), str(root))
    parts = rel.split(os.sep)
    return "/".join(unicodedata.normalize("NFC", part) for part in parts)


def size_difference_fraction(size_a: int, size_b: int) -> float:
    if size_a == 0 and size_b == 0:
        return 0.0
    largest = max(size_a, size_b)
    if largest == 0:
        return 1.0
    return abs(size_a - size_b) / largest


def _walk_files(
    root: Path,
    side_label: str,
    cancel_event: threading.Event,
    progress: ProgressCallback,
) -> Tuple[Dict[str, FileRecord], ErrorList]:
    records: Dict[str, FileRecord] = {}
    errors: ErrorList = []
    count = 0

    def onerror(error: OSError) -> None:
        path = error.filename or str(root)
        errors.append((str(path), str(error)))

    progress(f"Scanning {side_label}...", None, None)

    for dirpath, dirnames, filenames in os.walk(
        str(root), topdown=True, followlinks=False, onerror=onerror
    ):
        if cancel_event.is_set():
            raise CancelledError()

        dir_path = Path(dirpath)
        kept_dirs = []
        for dirname in dirnames:
            if is_ignored_name(dirname):
                continue
            full_path = dir_path / dirname
            try:
                if full_path.is_symlink():
                    continue
            except OSError as exc:
                errors.append((str(full_path), str(exc)))
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            if cancel_event.is_set():
                raise CancelledError()
            if is_ignored_name(filename):
                continue

            full_path = dir_path / filename
            try:
                stat_result = os.stat(str(full_path), follow_symlinks=False)
            except OSError as exc:
                errors.append((str(full_path), str(exc)))
                continue

            if stat.S_ISLNK(stat_result.st_mode) or not stat.S_ISREG(stat_result.st_mode):
                continue

            rel_path = normalize_rel_path(full_path, root)
            if rel_path in records:
                errors.append(
                    (
                        rel_path,
                        f"Duplicate normalized path in {side_label}; skipped {full_path}",
                    )
                )
                continue

            records[rel_path] = FileRecord(
                rel_path=rel_path,
                absolute_path=full_path,
                size=stat_result.st_size,
            )
            count += 1
            if count % PROGRESS_EVERY_FILES == 0:
                progress(f"Scanning {side_label}: {count} files", count, None)

    progress(f"Scanned {side_label}: {count} files", count, None)
    return records, errors


def scan_roots(
    root_a: Path,
    root_b: Path,
    cancel_event: Optional[threading.Event] = None,
    progress: ProgressCallback = noop_progress,
) -> ScanResult:
    if cancel_event is None:
        cancel_event = threading.Event()

    root_a = root_a.resolve()
    root_b = root_b.resolve()

    files_a, errors_a = _walk_files(root_a, "Folder A", cancel_event, progress)
    files_b, errors_b = _walk_files(root_b, "Folder B", cancel_event, progress)

    to_copy_a_to_b = [files_a[key] for key in sorted(files_a.keys() - files_b.keys())]
    to_copy_b_to_a = [files_b[key] for key in sorted(files_b.keys() - files_a.keys())]

    mismatches: List[SizeMismatch] = []
    for rel_path in sorted(files_a.keys() & files_b.keys()):
        size_a = files_a[rel_path].size
        size_b = files_b[rel_path].size
        fraction = size_difference_fraction(size_a, size_b)
        if fraction > SIZE_DIFF_THRESHOLD:
            mismatches.append(
                SizeMismatch(
                    rel_path=rel_path,
                    size_a=size_a,
                    size_b=size_b,
                    fraction=fraction,
                )
            )

    progress("Scan comparison complete", None, None)
    return ScanResult(
        to_copy_a_to_b=to_copy_a_to_b,
        to_copy_b_to_a=to_copy_b_to_a,
        size_mismatches=mismatches,
        errors=errors_a + errors_b,
    )
