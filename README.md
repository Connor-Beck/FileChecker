# FileChecker

FileChecker is a small Python/Tkinter app for comparing two folder trees and
copying missing files in both directions after a dry-run preview.

## Features

- Recursively scans both folders.
- Skips macOS metadata folders/files and ignores symlinks.
- Can optionally ignore files larger than 20 GB.
- Can require matching folder structure, or match duplicate files anywhere by
  filename and size.
- Can optionally check supported document files for corruption in one folder,
  or compare two folders and recommend copying a readable version over a
  corrupt one.
- Can create best-effort repaired copies for simple PDF and ZIP-document damage.
- Can make Folder B mirror Folder A by copying/replacing from A and moving extra
  files in B to Trash or Recycle Bin after confirmation.
- Can copy only files that exist in Folder A but not Folder B, preserving
  Folder A's relative folder structure without copying B back to A.
- Shows files missing from Folder B and files missing from Folder A.
- Flags files that exist on both sides when their sizes differ by more than 10%.
- Requires a visible preview before any copy runs.
- Preserves file metadata with `/bin/cp -p` on macOS/Linux when available, and
  `shutil.copy2` on Windows.
- Continues past per-file errors and reports them in the UI.

## Install From GitHub

Install Python 3 with Tkinter support, then clone the repository:

```sh
gh repo clone Connor-Beck/FileChecker
cd FileChecker
```

You can also clone with plain Git:

```sh
git clone https://github.com/Connor-Beck/FileChecker.git
cd FileChecker
```

### macOS/Linux

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m filechecker
```

### Windows Command Prompt

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m filechecker
```

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m filechecker
```

`source .venv/bin/activate` is for macOS/Linux shells. Windows uses the
`.venv\Scripts` activation commands above. If PowerShell blocks activation
scripts, either use Command Prompt or allow local scripts once with
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

You can also download the repository ZIP from GitHub, unzip it, open a terminal
in the extracted folder, and run the matching virtual environment and install
commands for your operating system.

## Run

From a terminal:

```sh
python3 -m filechecker
```

From Finder, double-click `run.command`. If macOS blocks the wrapper because it is
not executable, run this once from Terminal:

```sh
chmod +x run.command
```

On Windows, double-click `run.bat` from File Explorer after installing the
requirements.

## Usage

1. Choose folders:
   - Choose Folder A and Folder B to compare two folders.
   - Choose only one folder and enable `Check document corruption` to scan that
     folder for corrupt supported documents.
2. Choose the scan mode:
   - Checked: `Require same folder structure` compares files by relative path
     and copies missing folders/files into the same structure.
   - Unchecked: files count as duplicates when the filename and size match
     anywhere in the opposite folder.
   - `Check document corruption` validates supported document files and reports
     corrupt files. With two folders, it also reports cases where one side looks
     corrupt and the other side looks readable.
   - `Ignore files over 20 GB` leaves larger files out of scan results,
     copy/delete previews, size mismatches, and corruption checks.
   - `Make Folder B match Folder A` treats Folder A as the master. It copies
     missing files from A to B, replaces same-path files in B when their sizes
     differ, and moves files that exist only in B to Trash or Recycle Bin.
   - `Copy missing A -> B only` copies only relative paths that exist in Folder
     A and are missing from Folder B. It creates matching folders in Folder B,
     leaves B-only files alone, and does not overwrite size mismatches.
3. Click `Scan`.
4. Review the results:
   - `Will copy A -> B`
   - `Will copy B -> A`
   - `Will delete from Folder B`
   - `Size mismatches`
   - `Corruption results`
5. Click `Confirm Copy` or `Confirm Changes` to apply the previewed changes.

Outside Folder A master mode, size mismatches are never overwritten
automatically. Review them manually after the scan. If the destination path is
already occupied, FileChecker writes the new file with a `FileChecker copy`
suffix instead of overwriting it.

Corruption recommendations are advisory; `Confirm Copy` does not overwrite
corrupt files automatically. The corruption check currently supports PDFs and
ZIP-based document formats such as `docx`, `xlsx`, `pptx`, `odt`, `ods`, `odp`,
`epub`, `pages`, `numbers`, and `key`.

Use `Repair Selected` in the corruption results panel to create a repaired copy
beside the original. Simple PDF repairs can remove bytes before a recoverable
PDF header and add a missing EOF marker. ZIP-based document repairs rebuild a new
archive from readable members when the archive can still be opened. Deep file
format recovery is out of scope.

Use `Delete Selected` in the corruption results panel to move selected corrupt
files to Trash or Recycle Bin after confirmation.
