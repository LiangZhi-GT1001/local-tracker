from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import sys
from typing import Callable
import zipfile


RunCommand = Callable[[list[str]], None]


def validate_cli_archive(archive: Path) -> None:
    if not archive.is_file():
        raise FileNotFoundError(f"Bundled Local Tracker CLI archive not found: {archive}")
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    required = {
        "__main__.py",
        "src/cli.py",
        "markdown_it/__init__.py",
        "mdurl/__init__.py",
        "pygments/__init__.py",
        "rich/__init__.py",
        "tomli/__init__.py",
    }
    if not required.issubset(names):
        raise ValueError("Bundled Local Tracker CLI archive is incomplete")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Bundled Local Tracker CLI archive has unsafe path: {name}")


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _write_command(command: Path, installed_archive: Path) -> None:
    command.parent.mkdir(parents=True, exist_ok=True)
    if command.exists() and not command.is_file():
        raise IsADirectoryError(f"Cannot replace command directory: {command}")
    temporary = command.with_suffix(f"{command.suffix}.tmp")
    if os.name == "nt":
        temporary.write_text(
            f'@"{sys.executable}" "{installed_archive}" %*\r\n',
            encoding="utf-8",
        )
    else:
        temporary.write_text(
            "#!/bin/sh\n"
            f"exec {shlex.quote(sys.executable)} "
            f"{shlex.quote(str(installed_archive))} \"$@\"\n",
            encoding="utf-8",
        )
        temporary.chmod(0o755)
    temporary.replace(command)


def install_cli(
    archive: Path,
    *,
    data_home: Path,
    bin_dir: Path,
    run: RunCommand = _run,
) -> Path:
    if sys.version_info < (3, 10):
        raise RuntimeError("Local Tracker requires Python 3.10 or newer")
    validate_cli_archive(archive)

    data_home.mkdir(parents=True, exist_ok=True)
    installed_archive = data_home / "local-tracker-cli.zip"
    temporary_archive = installed_archive.with_suffix(".zip.tmp")
    shutil.copyfile(archive, temporary_archive)
    temporary_archive.replace(installed_archive)

    command = bin_dir / ("lt.cmd" if os.name == "nt" else "lt")
    _write_command(command, installed_archive)
    run([sys.executable, str(installed_archive), "--help"])
    return command


def main(argv: list[str] | None = None) -> int:
    skill_dir = Path(__file__).resolve().parent.parent
    default_data_home = Path(
        os.environ.get(
            "LOCAL_TRACKER_DATA_HOME",
            Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
            / "local-tracker",
        )
    )
    parser = argparse.ArgumentParser(description="Install the bundled Local Tracker CLI")
    parser.add_argument(
        "--cli-archive",
        type=Path,
        default=skill_dir / "assets" / "local-tracker-cli.zip",
    )
    parser.add_argument("--data-home", type=Path, default=default_data_home)
    parser.add_argument(
        "--bin-dir",
        type=Path,
        default=Path(
            os.environ.get("LOCAL_TRACKER_BIN_DIR", Path.home() / ".local" / "bin")
        ),
    )
    args = parser.parse_args(argv)

    command = install_cli(
        args.cli_archive.resolve(),
        data_home=args.data_home.expanduser().resolve(),
        bin_dir=args.bin_dir.expanduser().resolve(),
    )
    print(f"Local Tracker CLI installed: {command}")
    if str(command.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"Add {command.parent} to PATH before running lt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
