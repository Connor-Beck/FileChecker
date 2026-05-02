# FileChecker

FileChecker is a small macOS-friendly Python/Tkinter app for comparing two folder
trees and copying missing files in both directions after a dry-run preview.

## Features

- Recursively scans both folders.
- Skips macOS metadata folders/files and ignores symlinks.
- Shows files missing from Folder B and files missing from Folder A.
- Flags files that exist on both sides when their sizes differ by more than 10%.
- Requires a visible preview before any copy runs.
- Copies missing files with `/bin/cp -p` to preserve file metadata.
- Continues past per-file errors and reports them in the UI.

## Install From GitHub

Install Python 3, then clone the repository:

```sh
git clone https://github.com/Connor-Beck/FileChecker.git
cd FileChecker
```

Create and activate a virtual environment:

```sh
python3 -m venv .venv
source .venv/bin/activate
```

Install the optional dark theme dependency:

```sh
python3 -m pip install -r requirements.txt
```

You can also download the repository ZIP from GitHub, unzip it, open Terminal in
the extracted folder, and run the same virtual environment and install commands.

## Run

From Terminal:

```sh
python3 -m filechecker
```

From Finder, double-click `run.command`. If macOS blocks the wrapper because it is
not executable, run this once from Terminal:

```sh
chmod +x run.command
```

## Usage

1. Choose Folder A and Folder B.
2. Click `Scan`.
3. Review the dry-run preview:
   - `Will copy A -> B`
   - `Will copy B -> A`
   - `Size mismatches`
4. Click `Confirm Copy` to copy missing files.

Size mismatches are never overwritten automatically. Review them manually after
the scan.
