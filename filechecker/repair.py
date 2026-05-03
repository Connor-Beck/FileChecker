"""Best-effort document repair helpers."""

from __future__ import annotations

import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from .constants import PDF_EXTENSIONS, ZIP_DOCUMENT_EXTENSIONS


@dataclass(frozen=True)
class RepairResult:
    ok: bool
    source: Path
    repaired_path: Path | None
    message: str


def repair_document(path: Path) -> RepairResult:
    extension = path.suffix.lower()
    if extension in PDF_EXTENSIONS:
        return repair_pdf(path)
    if extension in ZIP_DOCUMENT_EXTENSIONS:
        return repair_zip_document(path)
    return RepairResult(False, path, None, "Unsupported document type")


def repair_pdf(path: Path) -> RepairResult:
    try:
        original = path.read_bytes()
    except OSError as exc:
        return RepairResult(False, path, None, str(exc))

    start = original.find(b"%PDF-")
    if start < 0:
        return RepairResult(False, path, None, "No PDF header found to recover")

    repaired = original[start:]
    changes = []
    if start:
        changes.append(f"removed {start} byte(s) before PDF header")

    if b"%%EOF" not in repaired[-4096:]:
        if repaired and not repaired.endswith(b"\n"):
            repaired += b"\n"
        repaired += b"%%EOF\n"
        changes.append("added missing PDF EOF marker")

    if repaired == original:
        return RepairResult(False, path, None, "No simple PDF repair was found")

    destination = unique_repaired_path(path)
    try:
        destination.write_bytes(repaired)
    except OSError as exc:
        return RepairResult(False, path, None, str(exc))

    return RepairResult(True, path, destination, "; ".join(changes))


def repair_zip_document(path: Path) -> RepairResult:
    destination = unique_repaired_path(path)
    copied = 0
    skipped = []

    try:
        with zipfile.ZipFile(path) as source:
            infos = source.infolist()
            if not infos:
                return RepairResult(False, path, None, "ZIP archive has no members")

            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as target:
                for info in infos:
                    try:
                        data = source.read(info.filename)
                    except (zipfile.BadZipFile, zlib.error, RuntimeError, EOFError) as exc:
                        skipped.append(f"{info.filename}: {exc}")
                        continue
                    target.writestr(info, data)
                    copied += 1
    except (zipfile.BadZipFile, zlib.error, RuntimeError, EOFError) as exc:
        return RepairResult(False, path, None, f"ZIP archive cannot be rebuilt: {exc}")
    except OSError as exc:
        return RepairResult(False, path, None, str(exc))

    if copied == 0:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        return RepairResult(False, path, None, "No readable ZIP members were recovered")

    if skipped:
        message = f"rebuilt archive with {copied} member(s); skipped {len(skipped)} corrupt member(s)"
    else:
        message = f"rebuilt archive with {copied} member(s)"
    return RepairResult(True, path, destination, message)


def unique_repaired_path(path: Path) -> Path:
    first = path.with_name(f"{path.stem} (FileChecker repaired){path.suffix}")
    if not first.exists():
        return first

    counter = 2
    while True:
        candidate = path.with_name(
            f"{path.stem} (FileChecker repaired {counter}){path.suffix}"
        )
        if not candidate.exists():
            return candidate
        counter += 1
