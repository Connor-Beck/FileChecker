# FileChecker

FileChecker is a small macOS-friendly Python/Tkinter app for comparing two folder
trees and copying missing files in both directions after a dry-run preview.

## Features

- Recursively scans both folders.
- Skips macOS metadata folders/files and ignores symlinks.
- Can require matching folder structure, or match duplicate files anywhere by
  filename and size.
- Can optionally check supported document files for corruption and recommend
  copying a readable version over a corrupt one.
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
2. Choose the scan mode:
   - Checked: `Require same folder structure` compares files by relative path
     and copies missing folders/files into the same structure.
   - Unchecked: files count as duplicates when the filename and size match
     anywhere in the opposite folder.
   - `Check document corruption` validates supported document files and reports
     cases where one side looks corrupt and the other side looks readable.
3. Click `Scan`.
4. Review the dry-run preview:
   - `Will copy A -> B`
   - `Will copy B -> A`
   - `Size mismatches`
   - `Corruption recommendations`
5. Click `Confirm Copy` to copy missing files.

Size mismatches are never overwritten automatically. Review them manually after
the scan. If the destination path is already occupied, FileChecker writes the
new file with a `FileChecker copy` suffix instead of overwriting it.

Corruption recommendations are advisory; `Confirm Copy` does not overwrite
corrupt files automatically. The corruption check currently supports PDFs and
ZIP-based document formats such as `docx`, `xlsx`, `pptx`, `odt`, `ods`, `odp`,
`epub`, `pages`, `numbers`, and `key`.
