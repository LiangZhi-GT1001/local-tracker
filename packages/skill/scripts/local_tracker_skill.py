from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any


AUDIT_SCHEMA = "lt-capability-audit-v1"
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "report"


class CliContractError(RuntimeError):
    pass


def parse_cli_json(output: str, expected_schema: str) -> dict[str, Any]:
    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise CliContractError("Local Tracker did not return valid JSON") from exc
    if not isinstance(data, dict) or data.get("schema_version") != expected_schema:
        raise CliContractError(
            f"Unsupported Local Tracker schema; expected {expected_schema}"
        )
    return validate_audit(data) if expected_schema == AUDIT_SCHEMA else data


def validate_audit(audit: dict[str, Any]) -> dict[str, Any]:
    summary = audit.get("summary")
    history_coverage = audit.get("history_coverage")
    coverage = audit.get("coverage")
    capabilities = audit.get("capabilities")
    if not isinstance(summary, dict) or any(
        not isinstance(summary.get(key, 0), int)
        for key in ("total", "review", "keep", "unknown")
    ):
        raise CliContractError("Invalid audit summary contract")
    if not isinstance(coverage, dict) or not isinstance(
        coverage.get("session_count", 0), int
    ):
        raise CliContractError("Invalid audit coverage contract")
    if not isinstance(history_coverage, dict) or not isinstance(
        history_coverage.get("session_count", 0), int
    ):
        raise CliContractError("Invalid audit history coverage contract")
    if not isinstance(capabilities, list):
        raise CliContractError("Invalid audit capabilities contract")
    for row in capabilities:
        if not isinstance(row, dict) or any(
            not isinstance(row.get(key, ""), str)
            for key in (
                "capability_id",
                "type",
                "name",
                "ownership",
                "state",
                "recommendation",
                "usage_tier",
            )
        ):
            raise CliContractError("Invalid audit capability contract")
        if row.get("usage_tier") not in {"frequent", "occasional", "not-detected"}:
            raise CliContractError("Invalid audit usage tier contract")
        if not isinstance(row.get("uses_per_10_days"), (int, float)):
            raise CliContractError("Invalid audit usage rate contract")
        if not isinstance(row.get("clients", []), list) or not all(
            isinstance(client, str) for client in row.get("clients", [])
        ):
            raise CliContractError("Invalid audit capability clients contract")
        if not isinstance(row.get("operation", {}), dict):
            raise CliContractError("Invalid audit capability operation contract")
    return audit


def run_lt(args: list[str]) -> dict[str, Any]:
    executable = os.environ.get("LT_BIN") or shutil.which("lt")
    if not executable:
        raise CliContractError("Local Tracker CLI `lt` is not installed or not on PATH")
    completed = subprocess.run(
        [executable, *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise CliContractError(f"Local Tracker command failed: {message}")
    if args[:1] == ["audit"]:
        return parse_cli_json(completed.stdout, AUDIT_SCHEMA)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CliContractError("Local Tracker mutation did not return valid JSON") from exc
    if not isinstance(result, dict):
        raise CliContractError("Local Tracker mutation returned an invalid payload")
    return result


def build_report_model(audit: dict[str, Any]) -> dict[str, Any]:
    if audit.get("schema_version") != AUDIT_SCHEMA:
        raise CliContractError(f"Unsupported Local Tracker schema; expected {AUDIT_SCHEMA}")
    validate_audit(audit)
    capabilities = [
        row
        for row in audit.get("capabilities", [])
        if row.get("ownership") == "user-installed"
    ]
    usage_groups = {
        tier: [row for row in capabilities if row.get("usage_tier") == tier]
        for tier in ("frequent", "occasional", "not-detected")
    }
    coverage = audit.get("coverage", {})
    history_coverage = audit.get("history_coverage", {})
    start = str(coverage.get("start", ""))[:10]
    end = str(coverage.get("end", ""))[:10]
    history_start = str(history_coverage.get("start", ""))[:10]
    history_end = str(history_coverage.get("end", ""))[:10]
    return {
        "audit": audit,
        "usage_groups": usage_groups,
        "inventory": capabilities,
        "history_coverage_label": (
            f"{history_start} - {history_end}"
            if history_start and history_end
            else "No observable records"
        ),
        "window_coverage_label": (
            f"{start} - {end}" if start and end else "No observable records"
        ),
    }


def build_report_audit(audit: dict[str, Any]) -> dict[str, Any]:
    model = build_report_model(audit)
    report_audit = dict(audit)
    report_audit["capabilities"] = model["inventory"]
    report_audit["summary"] = {
        "total": len(model["inventory"]),
        "review": sum(
            1
            for row in model["inventory"]
            if str(row.get("recommendation", "")).startswith("review-")
        ),
        "keep": sum(
            1 for row in model["inventory"] if row.get("recommendation") == "keep"
        ),
        "unknown": sum(
            1 for row in model["inventory"] if row.get("recommendation") == "unknown"
        ),
    }
    report_audit["usage_summary"] = {
        "frequent": len(model["usage_groups"]["frequent"]),
        "occasional": len(model["usage_groups"]["occasional"]),
        "not_detected": len(model["usage_groups"]["not-detected"]),
    }
    return report_audit


def write_static_report(audit: dict[str, Any], output_path: Path) -> Path:
    report_audit = build_report_audit(audit)
    html = (ASSET_DIR / "index.html").read_text(encoding="utf-8")
    css = (ASSET_DIR / "styles.css").read_text(encoding="utf-8")
    js = (ASSET_DIR / "app.js").read_text(encoding="utf-8")
    data = json.dumps(report_audit, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace(
        '<link rel="stylesheet" href="styles.css">',
        f"<style>\n{css}\n</style>",
    )
    html = html.replace(
        '<script src="app.js"></script>',
        (
            f"<script>window.__LOCAL_TRACKER_AUDIT__ = {data};</script>\n"
            f"<script>\n{js}\n</script>"
        ),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local Tracker static HTML report generator")
    subparsers = parser.add_subparsers(required=True)
    report = subparsers.add_parser("report")
    report.add_argument("--window", choices=["7d", "30d", "90d"], default="30d")
    report.add_argument("--audit-file", type=Path)
    report.add_argument(
        "--html-output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "local-tracker-capability-report.html",
    )
    args = parser.parse_args(argv)

    if args.audit_file:
        audit = parse_cli_json(args.audit_file.read_text(encoding="utf-8"), AUDIT_SCHEMA)
    else:
        audit = run_lt(["audit", "--window", args.window, "--format", "json"])
    path = write_static_report(audit, args.html_output)
    print(path.resolve().as_uri(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
