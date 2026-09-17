"""Post-generation EPUB validation with W3C EPUBCheck.

EPUBCheck spends ~5 s initialising its schemas on every run, so a small Java wrapper
(tools/epubcheck-server/EpubCheckServer.class) keeps one JVM warm per process and
validates on request in well under a second. If the wrapper is missing or misbehaves,
validation falls back to one `java -jar` run per EPUB.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)

TOOLS = Path(__file__).resolve().parents[2] / "tools"
TOOLS_JAR = TOOLS / "epubcheck" / "epubcheck.jar"
SERVER_DIR = TOOLS / "epubcheck-server"
TIMEOUT_SECONDS = 300
STARTUP_SECONDS = 60


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


def _problems(report: dict) -> list[str]:
    problems = []
    for message in report.get("messages", []):
        if message.get("severity") in ("ERROR", "FATAL"):
            where = message.get("locations") or [{}]
            location = where[0].get("path", "")
            problems.append(f"{message.get('ID')}: {message.get('message')} ({location})".strip())
    return problems


class _WarmValidator:
    """One long-lived EPUBCheck JVM, used by one caller at a time."""

    def __init__(self, java: str, jar: Path) -> None:
        self.java, self.jar = java, jar
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        atexit.register(self.stop)

    def _start(self) -> None:
        classpath = os.pathsep.join([str(self.jar), str(SERVER_DIR)])
        self.proc = subprocess.Popen(
            [self.java, "-Xmx512m", "-cp", classpath, "EpubCheckServer"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        if self._readline(STARTUP_SECONDS) != "ready":
            self.stop()
            raise ValidationUnavailable("EPUBCheck server did not start")

    def _readline(self, timeout: float) -> str | None:
        """A pipe read that can't hang the job if the JVM does."""
        result: list[str] = []
        reader = threading.Thread(target=lambda: result.append(self.proc.stdout.readline()), daemon=True)
        reader.start()
        reader.join(timeout)
        return result[0].strip() if result and result[0] else None

    def check(self, path: Path) -> list[str]:
        report = path.with_name(f"{path.name}.epubcheck.json")
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            try:
                self.proc.stdin.write(f"{path.resolve()}\t{report.resolve()}\n")
                self.proc.stdin.flush()
                code = self._readline(TIMEOUT_SECONDS)
            except (OSError, ValueError) as exc:
                self.stop()
                raise ValidationUnavailable(f"EPUBCheck server failed: {exc}") from exc
            if code is None:
                self.stop()
                raise ValidationUnavailable("EPUBCheck server timed out")
        try:
            if code not in ("0", "1") or not report.exists():
                raise ValidationUnavailable(f"EPUBCheck server returned {code!r}")
            return _problems(json.loads(report.read_text(encoding="utf-8")))
        finally:
            report.unlink(missing_ok=True)

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                proc.kill()


_warm: _WarmValidator | None = None
_warm_lock = threading.Lock()


def _warm_validator(java: str, jar: Path) -> _WarmValidator | None:
    if not (SERVER_DIR / "EpubCheckServer.class").exists():
        return None
    global _warm
    with _warm_lock:
        if _warm is None:
            _warm = _WarmValidator(java, jar)
        return _warm


def _one_shot(java: str, jar: Path, path: Path) -> list[str]:
    proc = subprocess.run(
        [java, "-XX:TieredStopAtLevel=1", "-jar", str(jar), str(path), "--json", "-", "--quiet"],
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
    )
    try:
        report = json.loads(proc.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise ValidationUnavailable(f"EPUBCheck produced no report: {proc.stderr[-500:]!r}") from exc
    return _problems(report)


def epubcheck(path: Path) -> list[str]:
    """Returns EPUBCheck's ERROR/FATAL messages (warnings are ignored)."""
    java, jar = _java(), _jar()
    if not java or not jar:
        raise ValidationUnavailable("EPUBCheck needs Java and backend/tools/epubcheck (scripts/fetch_epubcheck.py)")
    warm = _warm_validator(java, jar)
    if warm is not None:
        try:
            return warm.check(path)
        except ValidationUnavailable as exc:
            log.warning("warm EPUBCheck unavailable (%s); running it one-shot", exc)
    return _one_shot(java, jar, path)


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
