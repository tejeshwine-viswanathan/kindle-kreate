"""Download the W3C EPUBCheck validator into backend/tools/ (needs Java 11+ to run).

    python scripts/fetch_epubcheck.py
"""

import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "5.4.0"
URL = f"https://github.com/w3c/epubcheck/releases/download/v{VERSION}/epubcheck-{VERSION}.zip"
TOOLS = Path(__file__).resolve().parent.parent / "tools"


def main() -> None:
    target = TOOLS / "epubcheck"
    if (target / "epubcheck.jar").exists():
        print(f"already installed: {target / 'epubcheck.jar'}")
        return
    print(f"downloading {URL}")
    with urllib.request.urlopen(URL) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    TOOLS.mkdir(exist_ok=True)
    archive.extractall(TOOLS)
    shutil.rmtree(target, ignore_errors=True)
    (TOOLS / f"epubcheck-{VERSION}").rename(target)
    print(f"installed: {target / 'epubcheck.jar'}")


if __name__ == "__main__":
    sys.exit(main())
