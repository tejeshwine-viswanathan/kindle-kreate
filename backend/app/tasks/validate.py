"""Post-generation EPUB validation with W3C EPUBCheck."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..config import settings

TOOLS_JAR = Path(__file__).resolve().parents[2] / "tools" / "epubcheck" / "epubcheck.jar"
TIMEOUT_SECONDS = 300


class ValidationUnavailable(RuntimeError):
    pass


def _java() -> str | None:
    if settings.java_cmd:
        return settings.java_cmd
    if java_home := os.environ.get("JAVA_HOME"):
        candidate = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.exists():
            return str(candidate)
    if found := shutil.which("java"):
        return found
    # winget/MSI installs of Temurin don't always refresh PATH for running processes
    for root in (Path(r"C:\Program Files\Eclipse Adoptium"), Path(r"C:\Program Files\Java")):
        if root.exists():
            for candidate in sorted(root.glob("*/bin/java.exe"), reverse=True):
                return str(candidate)
    return None


def _jar() -> Path | None:
    jar = settings.epubcheck_jar or TOOLS_JAR
    return jar if jar.exists() else None


def validator_available() -> bool:
    return _java() is not None and _jar() is not None


def epubcheck(path: Path) -> list[str]:
    """Returns EPUBCheck's ERROR/FATAL messages (warnings are ignored)."""
    java, jar = _java(), _jar()
    if not java or not jar:
        raise ValidationUnavailable("EPUBCheck needs Java and backend/tools/epubcheck (scripts/fetch_epubcheck.py)")
    proc = subprocess.run(
        [java, "-jar", str(jar), str(path), "--json", "-", "--quiet"],
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
    )
    try:
        report = json.loads(proc.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise ValidationUnavailable(f"EPUBCheck produced no report: {proc.stderr[-500:]!r}") from exc
    problems = []
    for message in report.get("messages", []):
        if message.get("severity") in ("ERROR", "FATAL"):
            where = message.get("locations") or [{}]
            location = where[0].get("path", "")
            problems.append(f"{message.get('ID')}: {message.get('message')} ({location})".strip())
    return problems


def validate_epub(path: Path) -> None:
    """Raise if the EPUB is invalid, per the `epubcheck` setting (auto | required | off)."""
    if settings.epubcheck == "off":
        return
    if not validator_available():
        if settings.epubcheck == "required":
            raise ValidationUnavailable("EPUB validation is required but EPUBCheck is not installed.")
        return
    problems = epubcheck(path)
    if problems:
        shown = "; ".join(problems[:3]) + (f" (+{len(problems) - 3} more)" if len(problems) > 3 else "")
        raise ValueError(f"The generated EPUB failed validation: {shown}")
