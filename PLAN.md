# FileChecker — Development Plan

## Locked decisions

| # | Decision |
|---|---|
| 1 | Python 3 + Tkinter (`ttk`) GUI |
| 2 | Full recursive scan; skip macOS junk (`.DS_Store`, `._*`, `.Spotlight-V100`, `.Trashes`, `.fseventsd`, `.TemporaryItems`); ignore symlinks |
| 3 | Size diff = `abs(a-b)/max(a,b) > 0.10`; both-zero equal; one-zero flag |
| 4 | Bidirectional sync; mandatory dry-run preview before any copy |
| 5 | Copy via `/bin/cp -p` (preserves mtime, perms, xattrs, resource forks) |
| 6 | Single-window UI, dark mode via `sv-ttk` |
| 7 | Background worker thread; Cancel checked between files; partial-state reporting |
| 8 | Results on-screen only; no export |
| 9 | Skip-and-continue on per-file errors; errors panel at end |
| 10 | Run as script + `run.command` double-clickable wrapper |

## Project layout

```
FileChecker/
├── filechecker.py          # Single-file app (small enough to stay flat)
├── requirements.txt        # sv-ttk
├── run.command             # Finder-launchable shell wrapper
├── README.md               # Setup + usage
├── LICENSE                 # already exists
└── .gitattributes          # already exists
```

If `filechecker.py` grows past ~600 lines, split into:

```
filechecker/
├── __main__.py     # entrypoint
├── ui.py           # Tk widgets + layout
├── scanner.py      # recursive scan + diff logic
├── syncer.py       # cp -p execution + cancel handling
└── constants.py    # ignore list, threshold, etc.
```

## Module responsibilities

**`scanner`** — given two root paths, walks both trees, returns three lists:

- `to_copy_a_to_b: list[RelPath]`
- `to_copy_b_to_a: list[RelPath]`
- `size_mismatches: list[(RelPath, size_a, size_b, pct)]`

Pure function, no UI, no threading concerns. Reports progress via a callback.

**`syncer`** — given a copy plan + cancel-flag + progress callback, shells out to `cp -p` per file, creating intermediate directories with `os.makedirs(exist_ok=True)`. Returns `(copied: list, errors: list[(path, error_msg)])`.

**`ui`** — three-panel `ttk.Treeview` layout (will-copy A→B, will-copy B→A, size-mismatches; errors panel appears post-copy if non-empty). Buttons: Scan, Confirm Copy (disabled until scan), Cancel. Status bar with progress.

Threading: worker thread runs scan or copy, posts progress messages to a `queue.Queue`. UI uses `root.after(50, drain_queue)` to pull updates without blocking.

## Build order (milestones)

1. **Skeleton + theme** — single window, two Browse buttons, dark theme working. ~30 lines, sanity check that `sv-ttk` renders correctly.
2. **Scanner** — pure logic, no UI. Test from a Python REPL on two real folders. Verify ignore list, recursion, symlink skip, size-ratio math.
3. **Wire scanner → UI** — Scan button kicks off background thread, Treeviews populate, progress reports during scan. No copy yet; Confirm button disabled.
4. **Syncer** — `cp -p` execution with cancel flag and per-file progress. Test from REPL first.
5. **Wire syncer → UI** — Confirm Copy executes plan, progress bar updates, errors panel renders if anything fails. Cancel button works mid-copy.
6. **Polish** — sanity-check root-folder-name match (warn user if base names differ), confirm dialog wording, test on a deeply nested tree with macOS junk and a symlink, write `run.command`, verify Finder double-click launches it.

## Risks & things to watch for

- **`sv-ttk` quirks with `Treeview`** — themed Treeviews on macOS sometimes need explicit `style.configure("Treeview", ...)` for row colors to render right.
- **Path encoding** — macOS APFS uses NFD-normalized Unicode for filenames, but Python often hands back NFC. Comparing relative paths can falsely flag identical files as mismatched. Fix: normalize both sides with `unicodedata.normalize("NFC", ...)` when building the comparison keys.
- **Large folder trees** — building three full lists in memory is fine up to hundreds of thousands of files; will slow on 10M-file trees. Not solving in v1.
- **Cancel during Tkinter dialog** — Browse must be disabled during work to prevent UI desync.
- **`cp -p` exit codes** — non-zero stderr is the failure signal. Capture stderr and surface in the errors panel verbatim; no parsing.
- **Root-name-same check is a warning, not a block** — modal warning ("Root folder names differ: 'Photos' vs 'Photos_backup' — proceed?") is enough.

## Total scope estimate

~250–400 lines of Python. One sitting if uninterrupted; two if testing thoroughly on real data first.
