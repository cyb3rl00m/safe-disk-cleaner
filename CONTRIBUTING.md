# Contributing

Thanks for wanting to help! Contributions of any size are welcome — from a one-line description of
a cache folder to support for a new distribution.

## Easiest way to help: explain a cache folder

Folders in `~/.cache` that the app doesn't recognise are shown as "Usually safe" and are never
pre-selected. If you know what one of them is, either
[open an issue](../../issues/new?template=cache_folder.yml) or add one line to
[`src/safe_disk_cleaner/cache_info.py`](src/safe_disk_cleaner/cache_info.py):

```python
"folder-name": ("Friendly name", "What is stored there and what happens if it is deleted.", "safe", ("process-name",)),
```

The risk is `"safe"`, `"caution"` (usually safe) or `"review"` (review first). The last part lists
process names: while one of them is running, the folder is locked so it can't be deleted mid-use.

## Running from source

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pkexec   # Ubuntu/Debian
git clone https://github.com/cyb3rl00m/safe-disk-cleaner.git
cd safe-disk-cleaner
python3 safe-disk-cleaner            # the app
python3 safe-disk-cleaner --report   # read-only text report
```

## Tests

```bash
python3 -m unittest discover tests
```

`tests/test_safety.py` covers the rules (kernels, parsers, deletion limits) and
`tests/test_attacks.py` fuzzes the root helper with thousands of hostile requests.
Please make sure both pass before opening a pull request.

## Project layout

| Path | What it does |
|---|---|
| `src/safe_disk_cleaner/scanners.py` | Finds reclaimable space and holds all explanation texts. **Read-only.** |
| `src/safe_disk_cleaner/cache_info.py` | Descriptions of well-known `~/.cache` folders |
| `src/safe_disk_cleaner/policy.py` | Rules shared by app and helper: kernel keep-rule, validators, parsers |
| `src/safe_disk_cleaner/helper.py` | Privileged operations (runs as root) — re-validates everything |
| `src/safe_disk_cleaner/cleaner.py` | Turns your selection into a plan and runs it |
| `src/safe_disk_cleaner/window.py` | GTK 4 / libadwaita interface |
| `packaging/build-deb.sh` | Builds the `.deb` |

## Rules for code that runs as root

`helper.py` and `policy.py` decide what may be deleted as root. Changes there must:

1. accept only values from a whitelist or a strict regular expression,
2. re-check the system's current state instead of trusting what the app sends,
3. come with a test that tries to misuse the new code.

## Building the .deb

```bash
bash packaging/build-deb.sh     # → dist/safe-disk-cleaner_<version>_all.deb
```

Publishing a GitHub release builds the `.deb`, attaches it to the release and adds it to the signed
APT repository on the `gh-pages` branch (`packaging/publish-apt.sh` → `packaging/apt_repo.py`).
