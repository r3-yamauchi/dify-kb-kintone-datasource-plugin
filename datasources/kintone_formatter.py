# where: datasources/kintone_formatter.py
# what: Shared helpers to convert raw kintone record payloads into plain text strings.
# why: Keeps formatting logic isolated so it can be tested without loading the datasource runtime.

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class KintoneRecordFormatter:
    """Converts the nested kintone record payload into newline-delimited text."""

    @classmethod
    def render(cls, record: Mapping[str, Any]) -> str:
        lines: list[str] = []

        record_id = cls._extract_record_id(record)
        if record_id:
            lines.append(f"$id: {record_id}")

        for field_code in sorted(record.keys()):
            if field_code.startswith("$"):
                continue
            field_payload = record[field_code]
            if not isinstance(field_payload, Mapping):
                continue
            formatted_value = cls._format_value(field_payload)
            lines.append(f"{field_code}: {formatted_value}")

        return "\n".join(lines)

    @staticmethod
    def _extract_record_id(record: Mapping[str, Any]) -> str:
        record_meta = record.get("$id")
        if isinstance(record_meta, Mapping):
            value = record_meta.get("value")
            if value is not None:
                return str(value)
        return ""

    @classmethod
    def _format_value(cls, field: Mapping[str, Any]) -> str:
        field_type = field.get("type")
        value = field.get("value")

        if value is None:
            return ""

        if field_type == "SUBTABLE" and isinstance(value, list):
            rows: list[str] = []
            for row in value:
                if not isinstance(row, Mapping):
                    continue
                row_value = row.get("value")
                if not isinstance(row_value, Mapping):
                    continue
                cells = [
                    f"{cell_key}={cls._stringify(cell_payload.get('value'))}"
                    for cell_key, cell_payload in sorted(row_value.items())
                    if isinstance(cell_payload, Mapping)
                ]
                rows.append(", ".join(filter(None, cells)))
            return "; ".join(filter(None, rows))

        if isinstance(value, list):
            return ", ".join(
                cls._stringify(item.get("name") if isinstance(item, Mapping) else item)
                for item in value
            )

        if isinstance(value, Mapping):
            return ", ".join(
                f"{k}={cls._stringify(v)}"
                for k, v in sorted(value.items())
            )

        return cls._stringify(value)

    @staticmethod
    def _stringify(value: Any) -> str:
        if value is None:
            return ""
        return str(value)
