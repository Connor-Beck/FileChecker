"""Tkinter UI for FileChecker."""

from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .scanner import CancelledError, ErrorList, ScanResult, scan_roots
from .syncer import CopyTask, CopyOutcome, build_copy_tasks, copy_tasks


def format_bytes(size: int) -> str:
    value = float(size)
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{size} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


class FileCheckerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("FileChecker")
        self.root.geometry("1120x760")
        self.root.minsize(860, 560)

        self.queue: "queue.Queue[dict]" = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.busy_kind: Optional[str] = None

        self.folder_a = tk.StringVar()
        self.folder_b = tk.StringVar()
        self.require_same_structure = tk.BooleanVar(value=True)
        self.check_corruption = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="Choose two folders to scan.")

        self.scan_result: Optional[ScanResult] = None
        self.copy_plan: List[CopyTask] = []
        self.scan_errors: ErrorList = []
        self.browse_buttons: List[ttk.Button] = []

        self._apply_theme()
        self._build_layout()
        self.root.after(50, self._drain_queue)

    def _apply_theme(self) -> None:
        try:
            import sv_ttk  # type: ignore

            sv_ttk.set_theme("dark")
            return
        except Exception:
            pass

        self.root.configure(bg="#202124")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background="#202124", foreground="#f1f3f4")
        style.configure("TFrame", background="#202124")
        style.configure("TLabel", background="#202124", foreground="#f1f3f4")
        style.configure("TLabelframe", background="#202124", foreground="#f1f3f4")
        style.configure(
            "TLabelframe.Label", background="#202124", foreground="#f1f3f4"
        )
        style.configure("TButton", padding=(10, 6))
        style.configure("Treeview", background="#2b2c30", foreground="#f1f3f4")
        style.configure(
            "Treeview.Heading", background="#34363b", foreground="#f1f3f4"
        )
        style.map(
            "Treeview",
            background=[("selected", "#3f6ea8")],
            foreground=[("selected", "#ffffff")],
        )

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        outer = ttk.Frame(self.root, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        input_frame = ttk.Frame(outer)
        input_frame.grid(row=0, column=0, sticky="ew")
        input_frame.columnconfigure(1, weight=1)
        self.structure_check = ttk.Checkbutton(
            input_frame,
            text="Require same folder structure",
            variable=self.require_same_structure,
        )
        self.structure_check.grid(row=0, column=1, sticky="w", pady=(0, 6))
        self.corruption_check = ttk.Checkbutton(
            input_frame,
            text="Check document corruption",
            variable=self.check_corruption,
        )
        self.corruption_check.grid(row=0, column=2, sticky="e", pady=(0, 6))
        self._build_path_row(input_frame, 1, "Folder A", self.folder_a)
        self._build_path_row(input_frame, 2, "Folder B", self.folder_b)

        button_frame = ttk.Frame(outer)
        button_frame.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        button_frame.columnconfigure(3, weight=1)

        self.scan_button = ttk.Button(
            button_frame, text="Scan", command=self._start_scan
        )
        self.scan_button.grid(row=0, column=0, padx=(0, 8))

        self.copy_button = ttk.Button(
            button_frame,
            text="Confirm Copy",
            command=self._confirm_and_start_copy,
            state=tk.DISABLED,
        )
        self.copy_button.grid(row=0, column=1, padx=(0, 8))

        self.cancel_button = ttk.Button(
            button_frame, text="Cancel", command=self._cancel_work, state=tk.DISABLED
        )
        self.cancel_button.grid(row=0, column=2, padx=(0, 8))

        results_frame = ttk.Frame(outer)
        results_frame.grid(row=2, column=0, sticky="nsew")
        for column in range(3):
            results_frame.columnconfigure(column, weight=1)
        results_frame.rowconfigure(0, weight=1)

        self.tree_a_to_b = self._build_tree_panel(
            results_frame,
            0,
            "Will copy A -> B",
            (
                ("source", "Source Path", 220),
                ("destination", "Destination Path", 220),
                ("size", "Size", 80),
            ),
        )
        self.tree_b_to_a = self._build_tree_panel(
            results_frame,
            1,
            "Will copy B -> A",
            (
                ("source", "Source Path", 220),
                ("destination", "Destination Path", 220),
                ("size", "Size", 80),
            ),
        )
        self.tree_mismatches = self._build_tree_panel(
            results_frame,
            2,
            "Size mismatches",
            (
                ("path", "Relative Path", 280),
                ("size_a", "A Size", 80),
                ("size_b", "B Size", 80),
                ("diff", "Diff", 70),
            ),
        )

        self._build_errors_panel(outer)
        self._build_corruption_panel(outer)
        self._build_status_bar(outer)

    def _build_path_row(
        self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=3)
        button = ttk.Button(
            parent,
            text="Browse...",
            command=lambda target=variable: self._browse_folder(target),
        )
        button.grid(row=row, column=2, sticky="e", pady=3)
        self.browse_buttons.append(button)

    def _build_tree_panel(
        self,
        parent: ttk.Frame,
        column: int,
        title: str,
        columns: Sequence[Tuple[str, str, int]],
    ) -> ttk.Treeview:
        frame = ttk.Labelframe(parent, text=title)
        pad_left = 0 if column == 0 else 8
        frame.grid(row=0, column=column, sticky="nsew", padx=(pad_left, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        tree = ttk.Treeview(
            frame,
            columns=tuple(column_id for column_id, _, _ in columns),
            show="headings",
        )
        for column_id, heading, width in columns:
            tree.heading(column_id, text=heading)
            stretch = column_id in {"path", "source", "destination"}
            tree.column(column_id, width=width, stretch=stretch, anchor=tk.W)

        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        return tree

    def _build_errors_panel(self, parent: ttk.Frame) -> None:
        self.errors_frame = ttk.Labelframe(parent, text="Errors")
        self.errors_frame.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        self.errors_frame.columnconfigure(0, weight=1)
        self.errors_frame.rowconfigure(0, weight=1)

        self.errors_tree = ttk.Treeview(
            self.errors_frame,
            columns=("path", "error"),
            show="headings",
            height=5,
        )
        self.errors_tree.heading("path", text="Path")
        self.errors_tree.heading("error", text="Error")
        self.errors_tree.column("path", width=280, stretch=True)
        self.errors_tree.column("error", width=640, stretch=True)

        scrollbar = ttk.Scrollbar(
            self.errors_frame, orient=tk.VERTICAL, command=self.errors_tree.yview
        )
        self.errors_tree.configure(yscrollcommand=scrollbar.set)
        self.errors_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.errors_frame.grid_remove()

    def _build_corruption_panel(self, parent: ttk.Frame) -> None:
        self.corruption_frame = ttk.Labelframe(parent, text="Corruption recommendations")
        self.corruption_frame.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        self.corruption_frame.columnconfigure(0, weight=1)
        self.corruption_frame.rowconfigure(0, weight=1)

        self.corruption_tree = ttk.Treeview(
            self.corruption_frame,
            columns=("corrupt", "healthy", "recommendation", "reason"),
            show="headings",
            height=5,
        )
        self.corruption_tree.heading("corrupt", text="Corrupt File")
        self.corruption_tree.heading("healthy", text="Healthy Copy")
        self.corruption_tree.heading("recommendation", text="Recommendation")
        self.corruption_tree.heading("reason", text="Reason")
        self.corruption_tree.column("corrupt", width=260, stretch=True)
        self.corruption_tree.column("healthy", width=260, stretch=True)
        self.corruption_tree.column("recommendation", width=120, stretch=False)
        self.corruption_tree.column("reason", width=360, stretch=True)

        scrollbar = ttk.Scrollbar(
            self.corruption_frame, orient=tk.VERTICAL, command=self.corruption_tree.yview
        )
        self.corruption_tree.configure(yscrollcommand=scrollbar.set)
        self.corruption_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.corruption_frame.grid_remove()

    def _build_status_bar(self, parent: ttk.Frame) -> None:
        status_frame = ttk.Frame(parent)
        status_frame.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        status_frame.columnconfigure(0, weight=1)
        status_frame.columnconfigure(1, minsize=220)

        self.status_label = ttk.Label(status_frame, textvariable=self.status_text)
        self.status_label.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.progress = ttk.Progressbar(status_frame, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=1, sticky="ew")

    def _browse_folder(self, variable: tk.StringVar) -> None:
        selected = filedialog.askdirectory(parent=self.root, mustexist=True)
        if selected:
            variable.set(selected)

    def _start_scan(self) -> None:
        if self.busy_kind is not None:
            return

        raw_a = self.folder_a.get().strip()
        raw_b = self.folder_b.get().strip()
        if not raw_a or not raw_b:
            messagebox.showerror(
                "Choose folders",
                "Choose both Folder A and Folder B before scanning.",
                parent=self.root,
            )
            return

        root_a = Path(raw_a).expanduser()
        root_b = Path(raw_b).expanduser()
        if not self._validate_roots(root_a, root_b):
            return

        require_same_structure = self.require_same_structure.get()
        check_corruption = self.check_corruption.get()
        if require_same_structure and self._base_name(root_a) != self._base_name(root_b):
            proceed = messagebox.askyesno(
                "Root folder names differ",
                "Root folder names differ:\n\n"
                f"Folder A: {self._base_name(root_a)}\n"
                f"Folder B: {self._base_name(root_b)}\n\n"
                "Proceed with the scan?",
                parent=self.root,
            )
            if not proceed:
                return

        self._clear_results()
        self.scan_result = None
        self.copy_plan = []
        self.scan_errors = []
        if require_same_structure:
            status = "Scanning folder structure..."
        else:
            status = "Scanning duplicates by filename and size..."
        if check_corruption:
            status += " Document corruption check enabled."
        self._set_busy("scan", status)

        self.worker = threading.Thread(
            target=self._scan_worker,
            args=(root_a, root_b, require_same_structure, check_corruption),
            daemon=True,
        )
        self.worker.start()

    def _validate_roots(self, root_a: Path, root_b: Path) -> bool:
        for label, path in (("Folder A", root_a), ("Folder B", root_b)):
            if not path.exists():
                messagebox.showerror(
                    "Folder not found", f"{label} does not exist:\n{path}", parent=self.root
                )
                return False
            if not path.is_dir():
                messagebox.showerror(
                    "Not a folder", f"{label} is not a folder:\n{path}", parent=self.root
                )
                return False

        if root_a.resolve() == root_b.resolve():
            messagebox.showerror("Same folder", "Choose two different folders.", parent=self.root)
            return False

        return True

    def _scan_worker(
        self,
        root_a: Path,
        root_b: Path,
        require_same_structure: bool,
        check_corruption: bool,
    ) -> None:
        try:
            result = scan_roots(
                root_a,
                root_b,
                cancel_event=self.cancel_event,
                progress=self._post_progress,
                require_same_structure=require_same_structure,
                check_corruption=check_corruption,
            )
        except CancelledError:
            self.queue.put({"kind": "scan_cancelled"})
        except Exception:
            self.queue.put(
                {
                    "kind": "worker_error",
                    "title": "Scan failed",
                    "traceback": traceback.format_exc(),
                }
            )
        else:
            self.queue.put(
                {
                    "kind": "scan_done",
                    "result": result,
                    "root_a": root_a,
                    "root_b": root_b,
                }
            )

    def _confirm_and_start_copy(self) -> None:
        if self.busy_kind is not None or not self.copy_plan:
            return

        total = len(self.copy_plan)
        bytes_total = sum(task.size for task in self.copy_plan)
        if self.scan_result and self.scan_result.require_same_structure:
            mode_note = "Size mismatches are shown for review and will not be overwritten."
        else:
            mode_note = (
                "Files are treated as duplicates when the filename and size match "
                "anywhere in the opposite folder."
            )
        if self.scan_result and self.scan_result.corruption_findings:
            mode_note += (
                "\n\nCorruption recommendations are advisory and will not be "
                "copied automatically."
            )
        proceed = messagebox.askyesno(
            "Confirm Copy",
            "Dry-run preview is complete.\n\n"
            f"Copy {total} missing file(s) ({format_bytes(bytes_total)}) now?\n\n{mode_note}",
            parent=self.root,
        )
        if not proceed:
            return

        self._set_busy("copy", "Copying files...")
        self.worker = threading.Thread(
            target=self._copy_worker,
            args=(list(self.copy_plan),),
            daemon=True,
        )
        self.worker.start()

    def _copy_worker(self, tasks: List[CopyTask]) -> None:
        try:
            outcome = copy_tasks(
                tasks,
                cancel_event=self.cancel_event,
                progress=self._post_progress,
            )
        except Exception:
            self.queue.put(
                {
                    "kind": "worker_error",
                    "title": "Copy failed",
                    "traceback": traceback.format_exc(),
                }
            )
        else:
            self.queue.put({"kind": "copy_done", "outcome": outcome})

    def _cancel_work(self) -> None:
        if self.busy_kind is None:
            return
        self.cancel_event.set()
        self.status_text.set("Cancel requested; finishing the current file.")

    def _post_progress(
        self, message: str, current: Optional[int], total: Optional[int]
    ) -> None:
        self.queue.put(
            {
                "kind": "progress",
                "message": message,
                "current": current,
                "total": total,
            }
        )

    def _drain_queue(self) -> None:
        try:
            while True:
                self._handle_event(self.queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(50, self._drain_queue)

    def _handle_event(self, event: dict) -> None:
        kind = event.get("kind")
        if kind == "progress":
            self._handle_progress(event)
        elif kind == "scan_done":
            self._finish_scan(event["result"], event["root_a"], event["root_b"])
        elif kind == "scan_cancelled":
            self._clear_busy()
            self.status_text.set("Scan cancelled. Partial results were discarded.")
        elif kind == "copy_done":
            self._finish_copy(event["outcome"])
        elif kind == "worker_error":
            self._clear_busy()
            title = event.get("title", "Worker failed")
            details = event.get("traceback", "Unknown error")
            self.status_text.set(title)
            self._show_errors([(title, details)])
            messagebox.showerror(title, details, parent=self.root)

    def _handle_progress(self, event: dict) -> None:
        message = event.get("message") or ""
        current = event.get("current")
        total = event.get("total")
        self.status_text.set(message)

        if total:
            if str(self.progress.cget("mode")) != "determinate":
                self.progress.stop()
                self.progress.configure(mode="determinate")
            self.progress.configure(maximum=total)
            self.progress["value"] = current or 0

    def _finish_scan(self, result: ScanResult, root_a: Path, root_b: Path) -> None:
        self._clear_busy()
        self.scan_result = result
        self.scan_errors = list(result.errors)
        self.copy_plan = build_copy_tasks(result, root_a, root_b)

        self._populate_scan_results(result, self.copy_plan)
        self._update_copy_button()
        self.progress.configure(mode="determinate", maximum=1)
        self.progress["value"] = 1

        mode = "same structure" if result.require_same_structure else "duplicates anywhere"
        self.status_text.set(
            "Scan complete: "
            f"{mode}; "
            f"{len(result.to_copy_a_to_b)} A -> B, "
            f"{len(result.to_copy_b_to_a)} B -> A, "
            f"{len(result.size_mismatches)} size mismatch(es), "
            f"{len(result.corruption_findings)} corruption recommendation(s), "
            f"{len(result.errors)} error(s)."
        )

        if result.check_corruption:
            self._show_corruption_findings(result)
        else:
            self._hide_corruption_findings()

        if result.errors:
            self._show_errors(result.errors)
        else:
            self._hide_errors()

    def _finish_copy(self, outcome: CopyOutcome) -> None:
        self._clear_busy()
        combined_errors = self.scan_errors + outcome.errors
        self.progress.configure(mode="determinate", maximum=1)
        self.progress["value"] = 1

        if outcome.cancelled:
            status = (
                f"Copy cancelled after {len(outcome.copied)} of "
                f"{len(self.copy_plan)} file(s)."
            )
        else:
            status = (
                f"Copy complete: {len(outcome.copied)} of "
                f"{len(self.copy_plan)} file(s) copied."
            )

        if combined_errors:
            status += f" {len(combined_errors)} error(s)."
            self._show_errors(combined_errors)
        else:
            self._hide_errors()

        self.status_text.set(status)
        self._update_copy_button()

    def _set_busy(self, kind: str, status: str) -> None:
        self.busy_kind = kind
        self.cancel_event.clear()
        self.status_text.set(status)

        for button in self.browse_buttons:
            button.configure(state=tk.DISABLED)
        self.structure_check.configure(state=tk.DISABLED)
        self.corruption_check.configure(state=tk.DISABLED)
        self.scan_button.configure(state=tk.DISABLED)
        self.copy_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL)

        self.progress.stop()
        if kind == "scan":
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.configure(mode="determinate", maximum=max(1, len(self.copy_plan)))
            self.progress["value"] = 0

    def _clear_busy(self) -> None:
        self.busy_kind = None
        self.progress.stop()
        self.progress.configure(mode="determinate")

        for button in self.browse_buttons:
            button.configure(state=tk.NORMAL)
        self.structure_check.configure(state=tk.NORMAL)
        self.corruption_check.configure(state=tk.NORMAL)
        self.scan_button.configure(state=tk.NORMAL)
        self.cancel_button.configure(state=tk.DISABLED)
        self._update_copy_button()

    def _update_copy_button(self) -> None:
        if self.busy_kind is None and self.copy_plan:
            self.copy_button.configure(state=tk.NORMAL)
        else:
            self.copy_button.configure(state=tk.DISABLED)

    def _clear_results(self) -> None:
        for tree in (
            self.tree_a_to_b,
            self.tree_b_to_a,
            self.tree_mismatches,
            self.corruption_tree,
            self.errors_tree,
        ):
            tree.delete(*tree.get_children())
        self._hide_corruption_findings()
        self._hide_errors()
        self.progress.configure(mode="determinate", maximum=100)
        self.progress["value"] = 0

    def _populate_scan_results(
        self, result: ScanResult, copy_plan: List[CopyTask]
    ) -> None:
        for tree in (self.tree_a_to_b, self.tree_b_to_a, self.tree_mismatches):
            tree.delete(*tree.get_children())

        for task in copy_plan:
            tree = self.tree_a_to_b if task.direction == "A -> B" else self.tree_b_to_a
            tree.insert(
                "",
                tk.END,
                values=(
                    task.rel_path,
                    task.destination_rel_path,
                    format_bytes(task.size),
                ),
            )
        for mismatch in result.size_mismatches:
            self.tree_mismatches.insert(
                "",
                tk.END,
                values=(
                    mismatch.rel_path,
                    format_bytes(mismatch.size_a),
                    format_bytes(mismatch.size_b),
                    f"{mismatch.fraction:.1%}",
                ),
            )

    def _show_corruption_findings(self, result: ScanResult) -> None:
        self.corruption_tree.delete(*self.corruption_tree.get_children())
        for finding in result.corruption_findings:
            corrupt_label = (
                f"Folder {finding.corrupt_side}: {finding.corrupt_rel_path}"
            )
            healthy_label = (
                f"Folder {finding.healthy_side}: {finding.healthy_rel_path}"
            )
            self.corruption_tree.insert(
                "",
                tk.END,
                values=(
                    corrupt_label,
                    healthy_label,
                    finding.recommendation,
                    finding.reason,
                ),
            )
        self.corruption_frame.grid()

    def _hide_corruption_findings(self) -> None:
        self.corruption_tree.delete(*self.corruption_tree.get_children())
        self.corruption_frame.grid_remove()

    def _show_errors(self, errors: ErrorList) -> None:
        self.errors_tree.delete(*self.errors_tree.get_children())
        for path, error in errors:
            self.errors_tree.insert("", tk.END, values=(path, error))
        self.errors_frame.grid()

    def _hide_errors(self) -> None:
        self.errors_tree.delete(*self.errors_tree.get_children())
        self.errors_frame.grid_remove()

    @staticmethod
    def _base_name(path: Path) -> str:
        normalized = os.path.normpath(str(path.expanduser()))
        return os.path.basename(normalized) or normalized


def main() -> int:
    root = tk.Tk()
    FileCheckerApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
