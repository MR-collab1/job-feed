"""Build the audit slide deck from a computed report.

The slides are drawn by ``deck/build_deck.js`` with pptxgenjs, which produces
native PowerPoint charts that stay editable after the deck is opened.  This
module is the Python side: it hands the analytics payload over as JSON and
returns the path of the generated file.

The deck carries aggregate figures only; no patient identifier is written into
it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = PACKAGE_ROOT / "deck" / "build_deck.js"


def _node_executable() -> str:
    node = os.environ.get("ALCOHOL_AUDIT_NODE") or shutil.which("node")
    if not node:
        raise RuntimeError(
            "Node.js is required to build the slide deck but 'node' was not found on PATH. "
            "Install Node 18+ or set ALCOHOL_AUDIT_NODE."
        )
    return node


def build_deck(report: dict[str, Any], output_path: Path | str | None = None) -> Path:
    """Render ``report`` to a ``.pptx`` and return the path it was written to.

    ``output_path`` defaults to a file in a temporary directory, which suits the
    API's download endpoint. Pass a path to keep the deck somewhere durable.
    """
    if not GENERATOR.exists():
        raise RuntimeError(f"Deck generator not found at {GENERATOR}")

    destination = (
        Path(output_path)
        if output_path is not None
        else Path(tempfile.mkdtemp(prefix="alcohol-audit-")) / "alcohol-ciwa-audit.pptx"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(report, handle)
        payload = Path(handle.name)

    try:
        result = subprocess.run(
            [_node_executable(), str(GENERATOR), str(payload), str(destination)],
            capture_output=True,
            text=True,
            cwd=GENERATOR.parent,
            timeout=180,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Deck generation timed out after 180s") from error
    finally:
        payload.unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(
            "Deck generation failed.\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    if not destination.exists():
        raise RuntimeError(f"Deck generator reported success but {destination} is missing")
    return destination
