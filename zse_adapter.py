"""ZSE adapter — čita /opt/zarko/state/zse.json, ne ~/.hermes."""

from __future__ import annotations

from pathlib import Path

import json_adapter
from position import SourceResult

DEFAULT_FILENAME = "zse.json"


def load(path: str | Path) -> SourceResult:
    return json_adapter.load(path, expected_source="zse")
