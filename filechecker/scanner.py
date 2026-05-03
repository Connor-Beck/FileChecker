"""Recursive folder scanning and comparison logic."""

from __future__ import annotations

import os
import stat
import threading
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .constants import IGNORED_NAMES, IGNORED_PREFIXES, PROGRESS_EVERY_FILES
from .constants import PDF_EXTENSIONS, SIZE_DIFF_THRESHOLD, ZIP_DOCUMENT_EXTENSIONS

ProgressCallback = Callable[[str, Optional[int], Optional[int]], None]
ErrorList = List[Tuple[str, str]]
DuplicateKey = Tuple[str, int]


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
class DocumentStatus:
    record: FileRecord
    is_corrupt: bool
    reason: str


@dataclass(frozen=True)
class CorruptionFinding:
    corrupt_side: str
    corrupt_rel_path: str
    healthy_side: str
    healthy_rel_path: str
    recommendation: str
    reason: str
    corrupt_absolute_path: Optional[Path] = None
    healthy_absolute_path: Optional[Path] = None


@dataclass(frozen=True)
class ScanResult:
    to_copy_a_to_b: List[FileRecord]
    to_copy_b_to_a: List[FileRecord]
    size_mismatches: List[SizeMismatch]
    corruption_findings: List[CorruptionFinding]
    errors: ErrorList
    require_same_structure: bool
    check_corruption: bool
    to_replace_a_to_b: List[FileRecord] = field(default_factory=list)
    to_delete_b: List[FileRecord] = field(default_factory=list)
    mirror_a_to_b: bool = False
    single_folder_label: Optional[str] = None


def noop_progress(message: str, current: Optional[int], total: Optional[int]) -> None:
    del message, current, total


def is_ignored_name(name: str) -> bool:
    return name in IGNORED_NAMES or name.startswith(IGNORED_PREFIXES)


def normalize_rel_path(path: Path, root: Path) -> str:
    rel = os.path.relpath(str(path), str(root))
    parts = rel.split(os.sep)
    return "/".join(unicodedata.normalize("NFC", part) for part in parts)


def duplicate_key(record: FileRecord) -> DuplicateKey:
    filename = record.rel_path.rsplit("/", 1)[-1]
    return (filename, record.size)


def filename_key(record: FileRecord) -> str:
    return record.rel_path.rsplit("/", 1)[-1]


def size_difference_fraction(size_a: int, size_b: int) -> float:
    if size_a == 0 and size_b == 0:
        return 0.0
    largest = max(size_a, size_b)
    if largest == 0:
        return 1.0
    return abs(size_a - size_b) / largest


def is_supported_document(path: Path) -> bool:
    extension = path.suffix.lower()
    return extension in PDF_EXTENSIONS or extension in ZIP_DOCUMENT_EXTENSIONS


def check_document_status(record: FileRecord) -> Optional[DocumentStatus]:
    extension = record.absolute_path.suffix.lower()
    if extension in PDF_EXTENSIONS:
        return _check_pdf_status(record)
    if extension in ZIP_DOCUMENT_EXTENSIONS:
        return _check_zip_document_status(record)
    return None


def _check_pdf_status(record: FileRecord) -> DocumentStatus:
    try:
        with record.absolute_path.open("rb") as file:
            header = file.read(1024)
            if not header.startswith(b"%PDF-"):
                return DocumentStatus(record, True, "PDF header is missing")

            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            tail_size = min(file_size, 4096)
            file.seek(-tail_size, os.SEEK_END)
            tail = file.read(tail_size)
            if b"%%EOF" not in tail:
                return DocumentStatus(record, True, "PDF EOF marker is missing")
    except OSError as exc:
        return DocumentStatus(record, True, str(exc))

    return DocumentStatus(record, False, "PDF structure looks readable")


def _check_zip_document_status(record: FileRecord) -> DocumentStatus:
    try:
        with zipfile.ZipFile(record.absolute_path) as archive:
            bad_member = archive.testzip()
            if bad_member:
                return DocumentStatus(
                    record,
                    True,
                    f"ZIP member failed integrity check: {bad_member}",
                )
            _validate_zip_document_members(record, archive)
    except (zipfile.BadZipFile, zlib.error, RuntimeError, EOFError) as exc:
        return DocumentStatus(record, True, f"Bad ZIP document: {exc}")
    except OSError as exc:
        return DocumentStatus(record, True, str(exc))

    return DocumentStatus(record, False, "ZIP document archive looks readable")


def _validate_zip_document_members(
    record: FileRecord, archive: zipfile.ZipFile
) -> None:
    extension = record.absolute_path.suffix.lower()
    names = set(archive.namelist())

    if extension in {".docx", ".xlsx", ".pptx"} and "[Content_Types].xml" not in names:
        raise zipfile.BadZipFile("missing [Content_Types].xml")

    if extension in {
        ".odt",
        ".ods",
        ".odp",
        ".odg",
        ".odf",
        ".ott",
        ".ots",
        ".otp",
    } and not ({"mimetype", "META-INF/manifest.xml"} & names):
        raise zipfile.BadZipFile("missing ODF manifest")


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


def _check_corruption(
    files_a: Dict[str, FileRecord],
    files_b: Dict[str, FileRecord],
    require_same_structure: bool,
    cancel_event: threading.Event,
    progress: ProgressCallback,
) -> List[CorruptionFinding]:
    records = [
        record
        for record in sorted(
            list(files_a.values()) + list(files_b.values()),
            key=lambda item: item.rel_path,
        )
        if is_supported_document(record.absolute_path)
    ]
    statuses_a: Dict[str, DocumentStatus] = {}
    statuses_b: Dict[str, DocumentStatus] = {}
    paths_a = {record.absolute_path for record in files_a.values()}
    total = len(records)

    for index, record in enumerate(records, start=1):
        if cancel_event.is_set():
            raise CancelledError()
        progress(
            f"Checking document integrity {index}/{total}: {record.rel_path}",
            index - 1,
            total,
        )
        status = check_document_status(record)
        if status is None:
            continue
        if record.absolute_path in paths_a:
            statuses_a[record.rel_path] = status
        else:
            statuses_b[record.rel_path] = status
        progress(f"Checked document integrity {index}/{total}", index, total)

    if require_same_structure:
        findings = _corruption_findings_by_path(statuses_a, statuses_b)
    else:
        findings = _corruption_findings_by_filename(statuses_a, statuses_b)

    progress("Document integrity check complete", None, None)
    return findings


def _check_single_folder_corruption(
    files: Dict[str, FileRecord],
    side_label: str,
    cancel_event: threading.Event,
    progress: ProgressCallback,
) -> List[CorruptionFinding]:
    records = [
        record
        for record in sorted(files.values(), key=lambda item: item.rel_path)
        if is_supported_document(record.absolute_path)
    ]
    findings: List[CorruptionFinding] = []
    total = len(records)

    for index, record in enumerate(records, start=1):
        if cancel_event.is_set():
            raise CancelledError()
        progress(
            f"Checking document integrity {index}/{total}: {record.rel_path}",
            index - 1,
            total,
        )
        status = check_document_status(record)
        if status and status.is_corrupt:
            findings.append(
                CorruptionFinding(
                    corrupt_side=side_label,
                    corrupt_rel_path=record.rel_path,
                    healthy_side="",
                    healthy_rel_path="",
                    recommendation="Corrupt file found",
                    reason=status.reason,
                    corrupt_absolute_path=record.absolute_path,
                )
            )
        progress(f"Checked document integrity {index}/{total}", index, total)

    progress("Document integrity check complete", None, None)
    return findings


def _corruption_findings_by_path(
    statuses_a: Dict[str, DocumentStatus],
    statuses_b: Dict[str, DocumentStatus],
) -> List[CorruptionFinding]:
    findings: List[CorruptionFinding] = []
    for rel_path in sorted(statuses_a.keys() & statuses_b.keys()):
        finding = _build_corruption_finding(statuses_a[rel_path], statuses_b[rel_path])
        if finding:
            findings.append(finding)
    return findings


def _corruption_findings_by_filename(
    statuses_a: Dict[str, DocumentStatus],
    statuses_b: Dict[str, DocumentStatus],
) -> List[CorruptionFinding]:
    findings: List[CorruptionFinding] = []
    by_name_a = _statuses_by_filename(statuses_a)
    by_name_b = _statuses_by_filename(statuses_b)

    for filename in sorted(by_name_a.keys() & by_name_b.keys()):
        healthy_a = [status for status in by_name_a[filename] if not status.is_corrupt]
        corrupt_a = [status for status in by_name_a[filename] if status.is_corrupt]
        healthy_b = [status for status in by_name_b[filename] if not status.is_corrupt]
        corrupt_b = [status for status in by_name_b[filename] if status.is_corrupt]

        if healthy_b:
            for status in corrupt_a:
                finding = _build_corruption_finding(status, healthy_b[0])
                if finding:
                    findings.append(finding)
        if healthy_a:
            for status in corrupt_b:
                finding = _build_corruption_finding(healthy_a[0], status)
                if finding:
                    findings.append(finding)

    return findings


def _statuses_by_filename(
    statuses: Dict[str, DocumentStatus]
) -> Dict[str, List[DocumentStatus]]:
    by_name: Dict[str, List[DocumentStatus]] = {}
    for status in statuses.values():
        by_name.setdefault(filename_key(status.record), []).append(status)
    for status_list in by_name.values():
        status_list.sort(key=lambda item: item.record.rel_path)
    return by_name


def _build_corruption_finding(
    status_a: DocumentStatus, status_b: DocumentStatus
) -> Optional[CorruptionFinding]:
    if status_a.is_corrupt and not status_b.is_corrupt:
        return CorruptionFinding(
            corrupt_side="A",
            corrupt_rel_path=status_a.record.rel_path,
            healthy_side="B",
            healthy_rel_path=status_b.record.rel_path,
            recommendation="Re-copy B -> A",
            reason=status_a.reason,
            corrupt_absolute_path=status_a.record.absolute_path,
            healthy_absolute_path=status_b.record.absolute_path,
        )
    if status_b.is_corrupt and not status_a.is_corrupt:
        return CorruptionFinding(
            corrupt_side="B",
            corrupt_rel_path=status_b.record.rel_path,
            healthy_side="A",
            healthy_rel_path=status_a.record.rel_path,
            recommendation="Re-copy A -> B",
            reason=status_b.reason,
            corrupt_absolute_path=status_b.record.absolute_path,
            healthy_absolute_path=status_a.record.absolute_path,
        )
    return None


def scan_roots(
    root_a: Path,
    root_b: Path,
    cancel_event: Optional[threading.Event] = None,
    progress: ProgressCallback = noop_progress,
    require_same_structure: bool = True,
    check_corruption: bool = False,
    mirror_a_to_b: bool = False,
) -> ScanResult:
    if cancel_event is None:
        cancel_event = threading.Event()

    root_a = root_a.resolve()
    root_b = root_b.resolve()

    files_a, errors_a = _walk_files(root_a, "Folder A", cancel_event, progress)
    files_b, errors_b = _walk_files(root_b, "Folder B", cancel_event, progress)

    to_replace_a_to_b: List[FileRecord] = []
    to_delete_b: List[FileRecord] = []

    if mirror_a_to_b:
        to_copy_a_to_b = [
            files_a[key] for key in sorted(files_a.keys() - files_b.keys())
        ]
        to_copy_b_to_a = []
        to_replace_a_to_b = [
            files_a[key]
            for key in sorted(files_a.keys() & files_b.keys())
            if files_a[key].size != files_b[key].size
        ]
        to_delete_b = [
            files_b[key] for key in sorted(files_b.keys() - files_a.keys())
        ]
    elif require_same_structure:
        to_copy_a_to_b = [
            files_a[key] for key in sorted(files_a.keys() - files_b.keys())
        ]
        to_copy_b_to_a = [
            files_b[key] for key in sorted(files_b.keys() - files_a.keys())
        ]
    else:
        keys_a = {duplicate_key(record) for record in files_a.values()}
        keys_b = {duplicate_key(record) for record in files_b.values()}
        to_copy_a_to_b = [
            record
            for record in sorted(files_a.values(), key=lambda item: item.rel_path)
            if duplicate_key(record) not in keys_b
        ]
        to_copy_b_to_a = [
            record
            for record in sorted(files_b.values(), key=lambda item: item.rel_path)
            if duplicate_key(record) not in keys_a
        ]

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

    corruption_findings: List[CorruptionFinding] = []
    if check_corruption:
        corruption_findings = _check_corruption(
            files_a,
            files_b,
            require_same_structure,
            cancel_event,
            progress,
        )

    progress("Scan comparison complete", None, None)
    return ScanResult(
        to_copy_a_to_b=to_copy_a_to_b,
        to_copy_b_to_a=to_copy_b_to_a,
        size_mismatches=mismatches,
        corruption_findings=corruption_findings,
        errors=errors_a + errors_b,
        require_same_structure=require_same_structure,
        check_corruption=check_corruption,
        to_replace_a_to_b=to_replace_a_to_b,
        to_delete_b=to_delete_b,
        mirror_a_to_b=mirror_a_to_b,
    )


def scan_single_root_for_corruption(
    root: Path,
    side_label: str = "A",
    cancel_event: Optional[threading.Event] = None,
    progress: ProgressCallback = noop_progress,
) -> ScanResult:
    if cancel_event is None:
        cancel_event = threading.Event()

    root = root.resolve()
    files, errors = _walk_files(root, f"Folder {side_label}", cancel_event, progress)
    corruption_findings = _check_single_folder_corruption(
        files,
        side_label,
        cancel_event,
        progress,
    )

    progress("Single-folder scan complete", None, None)
    return ScanResult(
        to_copy_a_to_b=[],
        to_copy_b_to_a=[],
        size_mismatches=[],
        corruption_findings=corruption_findings,
        errors=errors,
        require_same_structure=True,
        check_corruption=True,
        single_folder_label=side_label,
    )
