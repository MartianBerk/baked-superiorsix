from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def format_tsv(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter="\t", lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return buffer.getvalue()
