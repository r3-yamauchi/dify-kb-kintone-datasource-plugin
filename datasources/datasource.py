"""Online drive datasource for exposing kintone attachments."""

# where: datasources/datasource.py
# what: Lists and downloads kintone attachment files through Dify's OnlineDriveDatasource interface.
# why: Enables Dify users to browse and retrieve files stored in kintone apps without duplicating storage.

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Generator, Mapping
import hashlib
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from dify_plugin.entities.datasource import (
    DatasourceMessage,
    OnlineDriveBrowseFilesRequest,
    OnlineDriveBrowseFilesResponse,
    OnlineDriveDownloadFileRequest,
    OnlineDriveFile,
    OnlineDriveFileBucket,
)
from dify_plugin.interfaces.datasource.online_drive import OnlineDriveDatasource

logger = logging.getLogger(__name__)

_DEFAULT_LOG_LEVEL = os.getenv("KINTONE_LOG_LEVEL")
if _DEFAULT_LOG_LEVEL:
    logging.basicConfig(level=getattr(logging, _DEFAULT_LOG_LEVEL.upper(), logging.INFO))


def _resolve_page_parameters(page: Any) -> Mapping[str, Any]:
    """Best-effort extraction of datasource parameters from a page request.

    Retained for backward compatibility with existing unit tests.
    """

    def _coerce(value: Any) -> Mapping[str, Any]:
        if isinstance(value, Mapping):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                return {}
            if isinstance(parsed, Mapping):
                return parsed
        return {}

    candidates: list[Any] = []
    for attr in ("datasource_parameters", "parameters", "datasource_parameter"):
        if hasattr(page, attr):
            candidates.append(getattr(page, attr))

    datasource = getattr(page, "datasource", None)
    if datasource is not None:
        for attr in ("parameters", "datasource_parameters", "datasource_parameter"):
            if hasattr(datasource, attr):
                candidates.append(getattr(datasource, attr))

    for candidate in candidates:
        mapping = _coerce(candidate)
        if mapping:
            return mapping

    return {}


class KintoneAPIError(RuntimeError):
    """Raised when the kintone REST API returns an error."""


class KintoneClient:
    """Minimal HTTP client for the kintone endpoints required by the datasource."""

    MAX_CHUNK: int = 500  # official API limit per request

    def __init__(self, base_url: str, api_token: str, timeout: int = 15) -> None:
        self.api_token = api_token
        self.timeout = timeout

        cleaned = base_url.rstrip("/")
        if "://" not in cleaned:
            cleaned = f"https://{cleaned}"
        self.base_url = cleaned.rstrip("/")

    def fetch_records(
        self,
        app_id: str,
        user_query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Mapping[str, Any]], int, int]:
        """Fetch a bounded slice of kintone records and return (records, total_count, next_offset)."""

        limit = max(1, min(limit, self.MAX_CHUNK))
        compound_query = self._compose_query(user_query, limit, offset)

        payload = self._request(
            "GET",
            "/k/v1/records.json",
            payload={
                "app": app_id,
                "query": compound_query,
                "totalCount": "true",
            },
        )

        records = payload.get("records", []) if isinstance(payload, Mapping) else []

        total_count_raw = payload.get("totalCount") if isinstance(payload, Mapping) else None
        try:
            total_count = int(total_count_raw)
        except (TypeError, ValueError):
            total_count = offset + len(records)

        next_offset = offset + len(records)
        return records, total_count, next_offset

    def get_record(self, app_id: str, record_id: str) -> Mapping[str, Any]:
        return self._request(
            "GET",
            "/k/v1/record.json",
            payload={"app": app_id, "id": record_id},
        )

    def download_file(self, file_key: str) -> bytes:
        """Download an attachment binary by fileKey."""

        url = urljoin(self.base_url + "/", "k/v1/file.json")
        query = urlencode({"fileKey": file_key})
        request = Request(
            url=f"{url}?{query}",
            method="GET",
            headers={
                "X-Cybozu-API-Token": self.api_token,
            },
        )

        logger.info("kintone download | file_key=%s", self._mask_secret(file_key))

        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", "ignore")
            raise KintoneAPIError(
                f"kintone file download failed with {exc.code}: {error_body or exc.reason}"
            ) from exc
        except URLError as exc:  # pragma: no cover - network failures surfaced directly
            raise KintoneAPIError(f"Failed to reach kintone for file download: {exc.reason}") from exc

    @staticmethod
    def _mask_secret(secret: str) -> str:
        if not secret:
            return "***"
        if len(secret) <= 6:
            return "***"
        return f"{secret[:3]}***{secret[-3:]}"

    def _compose_query(self, user_query: str | None, limit: int, offset: int) -> str:
        base = (user_query or "").strip()
        lower = base.lower()
        if " limit " in lower or lower.endswith(" limit"):
            return base
        clauses = [base] if base else []
        clauses.append(f"limit {limit}")
        if offset:
            clauses.append(f"offset {offset}")
        return " ".join(filter(None, clauses)).strip()

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        url = urljoin(self.base_url + "/", path.lstrip("/"))

        headers = {
            "X-Cybozu-API-Token": self.api_token,
            "X-HTTP-Method-Override": method.upper(),
            "Content-Type": "application/json",
        }
        data = json.dumps(payload or {}).encode("utf-8")

        request = Request(url=url, data=data, method="POST", headers=headers)
        masked_headers = {**headers, "X-Cybozu-API-Token": "***redacted***"}
        logger.info(
            "kintone request | method=%s override=%s path=%s payload=%s headers=%s",
            "POST",
            method.upper(),
            path,
            payload,
            masked_headers,
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", "ignore")
            raise KintoneAPIError(
                f"kintone API error {exc.code} for {path}: {error_body or exc.reason}"
            ) from exc
        except URLError as exc:  # pragma: no cover - network failures are surfaced directly
            raise KintoneAPIError(f"Failed to reach kintone: {exc.reason}") from exc

        return json.loads(payload or "{}")


class DifyKbKintoneDatasourcePluginDataSource(OnlineDriveDatasource):
    """Transforms kintone attachments into a browsable online drive."""

    DEFAULT_LIMIT = 100
    MAX_RECORD_SCAN = 5000

    def _browse_files(self, request: OnlineDriveBrowseFilesRequest) -> OnlineDriveBrowseFilesResponse:
        config = self._gather_configuration()
        app_id = self._resolve_app_id(config, request.bucket)
        query = self._optional_parameter(config, "query")
        attachment_codes = self._parse_attachment_field_codes(
            self._require_parameter(config, "attachment_field_codes", aliases=("attachmentFields",))
        )
        if not attachment_codes:
            raise ValueError("attachment_field_codes must contain at least one field code.")

        max_records = self._sanitize_record_limit(config.get("max_records"))
        max_files = self._determine_page_size(request.max_keys, max_records)
        offset, attachment_cursor = self._parse_pagination_state(request.next_page_parameters)
        debug_enabled = self._is_debug_enabled(config)

        bucket_id = self._compose_bucket_identifier(config, app_id)
        self._debug_log(
            debug_enabled,
            "Starting _browse_files",
            app_id=app_id,
            query=query,
            attachment_fields=attachment_codes,
            max_records=max_records,
            max_files=max_files,
            offset=offset,
            attachment_cursor=attachment_cursor,
            bucket_id=bucket_id,
        )

        client = self._build_client(config)
        files: list[OnlineDriveFile] = []
        next_offset = offset
        next_attachment_cursor = 0
        has_more = False
        total_count = None
        initial_offset = offset
        current_offset = offset
        current_attachment_cursor = attachment_cursor

        def _remaining_record_budget() -> int:
            return max(0, max_records - (current_offset - initial_offset))

        stop_processing = False
        while len(files) < max_files and _remaining_record_budget() > 0 and not stop_processing:
            request_limit = min(KintoneClient.MAX_CHUNK, _remaining_record_budget())
            try:
                records, batch_total, next_record_offset = client.fetch_records(app_id, query, request_limit, current_offset)
            except KintoneAPIError as exc:
                logger.exception("Failed to fetch kintone records during browse_files")
                raise ValueError(str(exc)) from exc

            if total_count is None:
                total_count = batch_total

            if not records:
                current_offset = next_record_offset
                break

            batch_base_offset = current_offset
            for index, record in enumerate(records):
                record_offset = batch_base_offset + index
                record_id = self._extract_record_id(record)
                if not record_id:
                    continue
                attachments = self._extract_attachments(record, attachment_codes)
                if not attachments and record_offset == batch_base_offset and current_attachment_cursor:
                    current_attachment_cursor = 0

                start_index = current_attachment_cursor if record_offset == batch_base_offset else 0
                current_attachment_cursor = 0

                for idx in range(start_index, len(attachments)):
                    attachment = attachments[idx]
                    files.append(
                        OnlineDriveFile(
                            id=self._compose_file_id(record_id, attachment["fileKey"]),
                            name=attachment["name"],
                            size=attachment["size"],
                            type="file",
                        )
                    )
                    if len(files) >= max_files:
                        more_records_remaining = (
                            idx + 1 < len(attachments)
                            or index + 1 < len(records)
                            or next_record_offset < total_count
                        )
                        if idx + 1 < len(attachments):
                            next_offset = record_offset
                            next_attachment_cursor = idx + 1
                        else:
                            next_offset = record_offset + 1
                            next_attachment_cursor = 0
                        has_more = more_records_remaining
                        stop_processing = True
                        break
                if stop_processing:
                    break

            current_offset = next_record_offset
            if current_offset == batch_base_offset:
                # defensive: avoid infinite loop
                break
            if total_count is not None and current_offset >= total_count:
                break
            if _remaining_record_budget() <= 0:
                # still more records left overall
                if total_count is None or current_offset < total_count:
                    has_more = True
                break

        if total_count is None:
            total_count = current_offset

        if not stop_processing and len(files) < max_files:
            if current_offset < total_count:
                has_more = True
                next_offset = current_offset
                next_attachment_cursor = 0
            else:
                has_more = False
                next_offset = current_offset
                next_attachment_cursor = 0

        if not files and not has_more:
            next_offset = offset
            next_attachment_cursor = 0

        next_page_parameters = None
        if has_more:
            next_page_parameters = {"offset": next_offset}
            if next_attachment_cursor:
                next_page_parameters["attachment_cursor"] = next_attachment_cursor

        bucket = bucket_id
        response = OnlineDriveBrowseFilesResponse(
            result=[
                OnlineDriveFileBucket(
                    bucket=bucket,
                    files=files,
                    is_truncated=bool(has_more),
                    next_page_parameters=next_page_parameters,
                )
            ]
        )

        self._debug_log(
            debug_enabled,
            "Completed _browse_files",
            returned=len(files),
            has_more=has_more,
            next_offset=next_offset,
            next_attachment_cursor=next_attachment_cursor,
            bucket_id=bucket,
        )
        return response

    def _download_file(
        self,
        request: OnlineDriveDownloadFileRequest,
    ) -> Generator[DatasourceMessage, None, None]:
        config = self._gather_configuration()
        app_id = self._resolve_app_id(config, request.bucket)
        bucket_id = self._compose_bucket_identifier(config, app_id)
        attachment_codes = self._parse_attachment_field_codes(
            self._require_parameter(config, "attachment_field_codes", aliases=("attachmentFields",))
        )
        if not attachment_codes:
            raise ValueError("attachment_field_codes must contain at least one field code.")

        record_id, file_key = self._split_file_identifier(request.id)
        debug_enabled = self._is_debug_enabled(config)
        self._debug_log(
            debug_enabled,
            "Starting _download_file",
            app_id=app_id,
            record_id=record_id,
            file_key=self._mask_secret(file_key),
            bucket_id=bucket_id,
        )

        client = self._build_client(config)
        try:
            payload = client.get_record(str(app_id), str(record_id))
        except KintoneAPIError as exc:
            logger.exception("Failed to fetch kintone record for download", extra={"record_id": record_id})
            raise ValueError(str(exc)) from exc

        record = payload.get("record")
        if not isinstance(record, Mapping):
            raise ValueError("kintone record payload is missing from response.")

        attachment = self._locate_attachment(record, attachment_codes, file_key)
        if attachment is None:
            raise ValueError("Attachment not found for the provided file ID.")

        self._debug_log(
            debug_enabled,
            "Downloaded attachment",
            file_name=attachment["name"],
            mime_type=attachment["contentType"],
            size=attachment["size"],
        )
        try:
            blob = client.download_file(file_key)
        except KintoneAPIError as exc:
            logger.exception("Failed to download kintone attachment", extra={"record_id": record_id})
            raise ValueError(str(exc)) from exc

        filename = str(attachment.get("name") or "attachment")
        mime_type = str(attachment.get("contentType") or "application/octet-stream")
        size = self._safe_int(attachment.get("size"))

        yield self.create_blob_message(
            blob,
            meta={
                "name": filename,
                "file_name": filename,
                "mime_type": mime_type,
                "size": size,
                "record_id": str(record_id),
                "app_id": str(app_id),
                "field_code": attachment.get("field_code"),
                "bucket_id": bucket_id,
            },
        )

    # Helpers -----------------------------------------------------------------

    def _build_client(self, parameters: Mapping[str, Any]) -> KintoneClient:
        base_url = self._resolve_kintone_base_url(parameters)
        api_token = self._resolve_kintone_api_token(parameters)
        return KintoneClient(base_url=base_url, api_token=api_token)

    def _gather_configuration(self) -> Mapping[str, Any]:
        combined: dict[str, Any] = {}
        runtime_state = {
            "runtime_type": type(self.runtime).__name__,
            "has_credentials": hasattr(self.runtime, "credentials"),
            "has_parameters": hasattr(self.runtime, "parameters"),
        }
        logger.debug("Runtime state snapshot: %s", runtime_state)
        credentials = self._ensure_mapping(getattr(self.runtime, "credentials", {}))
        combined.update(credentials)
        runtime_params = getattr(self.runtime, "parameters", None)
        if isinstance(runtime_params, Mapping):
            combined.update(self._ensure_mapping(runtime_params))
        else:
            logger.debug("runtime.parameters is unavailable or not a mapping: %r", runtime_params)

        # Apply provider defaults if overrides are missing
        defaults_map = (
            ("app_id", "default_app_id"),
            ("attachment_field_codes", "default_attachment_field_codes"),
            ("query", "default_query"),
            ("max_records", "default_max_records"),
            ("debug_logging", "default_debug_logging"),
        )
        for key, default_key in defaults_map:
            if key not in combined and default_key in combined and combined[default_key] not in (None, ""):
                combined[key] = combined[default_key]

        return combined

    @staticmethod
    def _ensure_mapping(candidate: Any) -> Mapping[str, Any]:
        return candidate if isinstance(candidate, Mapping) else {}

    def _resolve_app_id(self, parameters: Mapping[str, Any], bucket: str | None) -> str:
        configured = self._require_parameter(parameters, "app_id", aliases=("workspace_id", "bucket"))
        if bucket:
            bucket_str = str(bucket).strip()
            if bucket_str and bucket_str != configured:
                logger.warning("Bucket/app_id mismatch detected: configured=%s requested=%s", configured, bucket_str)
            if bucket_str:
                return bucket_str
        return configured

    def _require_parameter(self, parameters: Mapping[str, Any], key: str, *, aliases: tuple[str, ...] = ()) -> str:
        value = self._extract_parameter(parameters, key, aliases=aliases)
        if value is None:
            runtime_params = getattr(self.runtime, "parameters", None)
            if isinstance(runtime_params, Mapping):
                value = self._extract_parameter(runtime_params, key, aliases=aliases)
        if value is None:
            credentials_snapshot: Mapping[str, Any] | None = getattr(self.runtime, "credentials", None)
            logger.error(
                "Required parameter missing: key=%s aliases=%s parameters=%r runtime=%r credentials=%r",
                key,
                aliases,
                parameters,
                getattr(self.runtime, "parameters", None),
                credentials_snapshot,
            )
            raise ValueError(f"Parameter '{key}' is required.")
        return value

    def _optional_parameter(
        self,
        parameters: Mapping[str, Any],
        key: str,
        *,
        aliases: tuple[str, ...] = (),
    ) -> str | None:
        return self._extract_parameter(parameters, key, aliases=aliases)

    def _join_api_tokens(self, raw_tokens: str) -> str:
        tokens = [
            fragment.strip()
            for fragment in re.split(r"[,\n]+", raw_tokens)
            if fragment and fragment.strip()
        ]
        if not tokens:
            raise ValueError("kintone_api_token credential must contain at least one token.")
        if len(tokens) > 9:
            raise ValueError("kintone_api_token credential supports up to 9 tokens.")
        return "\n".join(tokens)

    def _resolve_kintone_base_url(self, parameters: Mapping[str, Any]) -> str:
        override = self._optional_parameter(parameters, "kintone_base_url", aliases=("base_url",))
        if override:
            return override
        raise ValueError("kintone_base_url must be provided in the datasource configuration.")

    def _resolve_kintone_api_token(self, parameters: Mapping[str, Any]) -> str:
        override = self._optional_parameter(parameters, "kintone_api_token", aliases=("api_token",))
        if override:
            return self._join_api_tokens(override)
        raise ValueError("kintone_api_token must be provided in the datasource configuration.")

    def _compose_bucket_identifier(self, parameters: Mapping[str, Any], app_id: str) -> str:
        base_url = self._resolve_kintone_base_url(parameters)
        parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
        host_path = (parsed.netloc + parsed.path).lower()
        safe = re.sub(r"[^a-z0-9]+", "_", host_path).strip("_")
        if not safe:
            safe = "kintone"
        if len(safe) > 48:
            digest = hashlib.sha1(host_path.encode("utf-8")).hexdigest()[:10]
            safe = f"kintone_{digest}"
        return f"{safe}_{app_id}"

    def _sanitize_record_limit(self, raw_limit: Any) -> int:
        try:
            limit = int(raw_limit or self.DEFAULT_LIMIT)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        if limit <= 0:
            raise ValueError("max_records must be a positive integer")
        return min(limit, self.MAX_RECORD_SCAN)

    def _determine_page_size(self, requested: int | None, fallback: int) -> int:
        try:
            if requested is None:
                return fallback
            return max(1, int(requested))
        except (TypeError, ValueError):
            return fallback

    def _is_debug_enabled(self, parameters: Mapping[str, Any] | None) -> bool:
        value: Any | None = None
        if parameters:
            value = parameters.get("debug_logging")
        if value is None:
            runtime_params = getattr(self.runtime, "parameters", None)
            if isinstance(runtime_params, Mapping):
                value = runtime_params.get("debug_logging")
        return bool(value)

    def _parse_attachment_field_codes(self, raw_codes: str) -> list[str]:
        if not raw_codes:
            return []
        codes = []
        for code in raw_codes.split(","):
            cleaned = code.strip()
            if cleaned and cleaned not in codes:
                codes.append(cleaned)
        return codes

    @staticmethod
    def _parse_pagination_state(next_page: Mapping[str, Any] | None) -> tuple[int, int]:
        offset = 0
        cursor = 0
        if isinstance(next_page, Mapping):
            try:
                if next_page.get("offset") is not None:
                    offset = max(0, int(next_page["offset"]))
            except (TypeError, ValueError):
                offset = 0
            try:
                if next_page.get("attachment_cursor") is not None:
                    cursor = max(0, int(next_page["attachment_cursor"]))
            except (TypeError, ValueError):
                cursor = 0
        return offset, cursor

    def _extract_parameter(self, parameters: Mapping[str, Any], key: str, *, aliases: tuple[str, ...]) -> str | None:
        if not isinstance(parameters, Mapping):
            return None
        for candidate in self._candidate_keys(key, aliases):
            if candidate not in parameters:
                continue
            value = parameters.get(candidate)
            if value is None:
                continue
            value_str = str(value).strip()
            if value_str:
                return value_str
        return None

    @staticmethod
    def _candidate_keys(key: str, aliases: tuple[str, ...]) -> list[str]:
        candidates = [key, *aliases]
        parts = key.split("_")
        camel = "".join([parts[0]] + [part.capitalize() for part in parts[1:]]) if parts else key
        if camel and camel not in candidates:
            candidates.append(camel)
        hyphenated = key.replace("_", "-")
        if hyphenated not in candidates:
            candidates.append(hyphenated)
        return candidates

    @staticmethod
    def _extract_record_id(record: Mapping[str, Any]) -> str:
        payload = record.get("$id")
        if isinstance(payload, Mapping) and payload.get("value") is not None:
            return str(payload["value"])
        return ""

    def _extract_attachments(
        self,
        record: Mapping[str, Any],
        attachment_codes: Iterable[str],
    ) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        for field_code in attachment_codes:
            field_payload = record.get(field_code)
            if not isinstance(field_payload, Mapping):
                continue
            values = field_payload.get("value")
            if not isinstance(values, list):
                continue
            for entry in values:
                if not isinstance(entry, Mapping):
                    continue
                file_key = entry.get("fileKey")
                if not file_key:
                    continue
                attachment = {
                    "fileKey": str(file_key),
                    "name": str(entry.get("name") or f"{field_code}-{file_key}"),
                    "size": self._safe_int(entry.get("size")),
                    "contentType": str(entry.get("contentType") or "application/octet-stream"),
                    "field_code": field_code,
                }
                attachments.append(attachment)
        return attachments

    def _locate_attachment(
        self,
        record: Mapping[str, Any],
        attachment_codes: Iterable[str],
        file_key: str,
    ) -> dict[str, Any] | None:
        for attachment in self._extract_attachments(record, attachment_codes):
            if attachment["fileKey"] == file_key:
                return attachment
        return None

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _compose_file_id(record_id: str, file_key: str) -> str:
        return f"{record_id}:{file_key}"

    @staticmethod
    def _split_file_identifier(identifier: str) -> tuple[str, str]:
        if ":" not in identifier:
            raise ValueError("File ID format is invalid. Expected '<record_id>:<file_key>'.")
        record_id, file_key = identifier.split(":", 1)
        record_id = record_id.strip()
        file_key = file_key.strip()
        if not record_id or not file_key:
            raise ValueError("File ID must include both record_id and file_key.")
        return record_id, file_key

    @staticmethod
    def _mask_secret(secret: str) -> str:
        if not secret:
            return "***"
        if len(secret) <= 6:
            return "***"
        return f"{secret[:3]}***{secret[-3:]}"

    def _debug_log(self, enabled: bool, message: str, **extra: Any) -> None:
        if not enabled:
            return
        if extra:
            logger.info("[debug] %s | extra=%s", message, extra)
        else:
            logger.info("[debug] %s", message)
