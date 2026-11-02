from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import zipfile


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_NAMES = {
    ".DS_Store",
    ".env",
    ".git",
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    "__pycache__",
}
FORBIDDEN_SUFFIXES = {".log", ".pyc", ".pyo", ".tmp", ".zip"}
SENSITIVE_PATTERNS = (
    re.compile(rb"/Users/[A-Za-z0-9._-]+/"),
    re.compile(rb"sk-(?=[A-Za-z0-9_-]{16,})(?=[A-Za-z0-9_-]*[0-9])[A-Za-z0-9_-]+"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY"),
)
CLI_RUNTIME_PREFIXES = ("markdown_it/", "mdurl/", "pygments/", "rich/", "tomli/")
CLI_WHEELS = (
    "markdown_it_py-4.2.0-py3-none-any.whl",
    "mdurl-0.1.2-py3-none-any.whl",
    "pygments-2.20.0-py3-none-any.whl",
    "rich-15.0.0-py3-none-any.whl",
    "tomli-2.2.1-py3-none-any.whl",
)
CLI_BOOTSTRAP = b"from src.cli import main\n\nraise SystemExit(main())\n"


def _validate_content(data: bytes, source: str) -> None:
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(data):
            raise ValueError(f"Sensitive content detected in {source}")


def _write_entry(
    archive: zipfile.ZipFile,
    archive_name: str,
    data: bytes,
    *,
    executable: bool = False,
) -> None:
    info = zipfile.ZipInfo(archive_name, date_time=(1980, 1, 1, 0, 0, 0))
    info.external_attr = (0o100755 if executable else 0o100644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data)


def _cli_zip_bytes() -> bytes:
    output = io.BytesIO()
    seen = {"__main__.py"}
    _validate_content(CLI_BOOTSTRAP, "generated CLI bootstrap")

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_entry(archive, "__main__.py", CLI_BOOTSTRAP)

        sources = [ROOT / "LICENSE"]
        sources.extend(
            path
            for path in sorted((ROOT / "packages/cli/src").rglob("*"))
            if path.is_file() and _is_publishable(path)
        )
        for source in sources:
            archive_name = (
                "LICENSE"
                if source == ROOT / "LICENSE"
                else source.relative_to(ROOT / "packages/cli").as_posix()
            )
            if archive_name in seen:
                raise ValueError(f"Duplicate CLI release path: {archive_name}")
            seen.add(archive_name)
            data = source.read_bytes()
            _validate_content(data, str(source))
            _write_entry(archive, archive_name, data)

        wheel_dir = ROOT / "packages/cli/vendor/wheels"
        wheels = sorted(wheel_dir.glob("*.whl"))
        if tuple(wheel.name for wheel in wheels) != CLI_WHEELS:
            raise ValueError("Vendored CLI wheels do not match the approved release set")
        for wheel in wheels:
            with zipfile.ZipFile(wheel) as package:
                for name in sorted(package.namelist()):
                    if name.endswith("/"):
                        continue
                    path = PurePosixPath(name)
                    if path.is_absolute() or ".." in path.parts:
                        raise ValueError(f"Unsafe path in vendored wheel {wheel.name}: {name}")
                    is_runtime = name.startswith(CLI_RUNTIME_PREFIXES)
                    is_license = ".dist-info/" in name and (
                        "LICENSE" in path.name.upper()
                        or "COPYING" in path.name.upper()
                        or path.name.upper() == "AUTHORS"
                    )
                    if not is_runtime and not is_license:
                        continue
                    archive_name = (
                        name
                        if is_runtime
                        else f"licenses/{wheel.stem}/{path.name}"
                    )
                    if archive_name in seen:
                        raise ValueError(f"Duplicate CLI release path: {archive_name}")
                    seen.add(archive_name)
                    data = package.read(name)
                    _validate_content(data, f"{wheel.name}:{name}")
                    _write_entry(archive, archive_name, data)
    return output.getvalue()


def _skill_files() -> list[tuple[Path, Path]]:
    files = [
        (
            ROOT / "packages/skill/SKILL.md",
            Path("SKILL.md"),
        )
    ]
    for relative in ("agents", "assets", "scripts"):
        base = ROOT / "packages/skill" / relative
        for path in sorted(base.rglob("*")):
            if path.is_file() and _is_publishable(path):
                files.append((path, path.relative_to(base.parent)))
    return files


def _is_publishable(path: Path) -> bool:
    if any(part in FORBIDDEN_NAMES for part in path.parts):
        return False
    return path.suffix not in FORBIDDEN_SUFFIXES


def _validate(files: list[tuple[Path, Path]]) -> None:
    seen: set[Path] = set()
    for source, archive_name in files:
        if not source.is_file():
            raise FileNotFoundError(f"Missing release input: {source}")
        if archive_name in seen:
            raise ValueError(f"Duplicate release path: {archive_name}")
        seen.add(archive_name)
        if not _is_publishable(archive_name):
            raise ValueError(f"Forbidden release path: {archive_name}")
        _validate_content(source.read_bytes(), str(source))


def _write_marketplace_zip(destination: Path) -> None:
    skill_files = _skill_files()
    cli_bundle = _cli_zip_bytes()
    _validate(skill_files)

    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, archive_name in skill_files:
            _write_entry(
                archive,
                str(archive_name),
                source.read_bytes(),
                executable=bool(source.stat().st_mode & 0o100),
            )
        cli_info = zipfile.ZipInfo(
            "assets/local-tracker-cli.zip",
            date_time=(1980, 1, 1, 0, 0, 0),
        )
        cli_info.external_attr = 0o100644 << 16
        cli_info.compress_type = zipfile.ZIP_STORED
        archive.writestr(cli_info, cli_bundle)
    temporary.replace(destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "target",
    )
    args = parser.parse_args()

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    skill_zip = output_dir / f"local-tracker-skill-v{version}.zip"
    _write_marketplace_zip(skill_zip)
    outputs = [skill_zip]

    checksums = output_dir / "SHA256SUMS"
    temporary_checksums = checksums.with_suffix(".tmp")
    temporary_checksums.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in outputs),
        encoding="ascii",
    )
    temporary_checksums.replace(checksums)
    outputs.append(checksums)

    for path in outputs:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
