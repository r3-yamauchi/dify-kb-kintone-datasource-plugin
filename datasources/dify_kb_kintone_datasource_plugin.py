"""Online document datasource for kintone records."""

# where: datasources/dify_kb_kintone_datasource_plugin.py
# what: Maps kintone app records to Dify online documents, exposing each record as a page.
# why: Enables the knowledge base to index kintone content without duplicating business logic elsewhere.

from __future__ import annotations

import json
import logging
import os
from collections.abc import Generator, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from dify_plugin.entities.datasource import (
    DatasourceGetPagesResponse,
    DatasourceMessage,
    GetOnlineDocumentPageContentRequest,
    OnlineDocumentInfo,
    OnlineDocumentPage,
)
from dify_plugin.interfaces.datasource.online_document import OnlineDocumentDatasource

from datasources.kintone_formatter import KintoneRecordFormatter

_DEFAULT_LOG_LEVEL = os.getenv("KINTONE_LOG_LEVEL")
if _DEFAULT_LOG_LEVEL:
    logging.basicConfig(level=getattr(logging, _DEFAULT_LOG_LEVEL.upper(), logging.INFO))

logger = logging.getLogger(__name__)


class KintoneAPIError(RuntimeError):
    """Raised when the kintone REST API returns an error."""


class KintoneClient:
    """Minimal HTTP client for the handful of kintone endpoints we need."""

    MAX_CHUNK: int = 500  # official API limit per request

    def __init__(self, base_url: str, api_token: str, timeout: int = 15) -> None:
        self.base_url = base_url
        self.api_token = api_token
        self.timeout = timeout
        cleaned = self.base_url.rstrip("/")
        if "://" not in cleaned:
            cleaned = f"https://{cleaned}"
        self.base_url = cleaned.rstrip("/")

    def get_app(self, app_id: str) -> Mapping[str, Any]:
        return self._request("GET", "/k/v1/app.json", payload={"id": app_id})

    def iter_records(
        self,
        app_id: str,
        user_query: str | None,
        desired_total: int,
    ) -> list[Mapping[str, Any]]:
        collected: list[Mapping[str, Any]] = []
        offset = 0

        while len(collected) < desired_total:
            current_limit = min(self.MAX_CHUNK, desired_total - len(collected))
            compound_query = self._compose_query(user_query, current_limit, offset)
            payload = self._request(
                "GET",
                "/k/v1/records.json",
                payload={
                    "app": app_id,
                    "query": compound_query,
                    "totalCount": "true",
                },
            )

            records = payload.get("records", [])
            collected.extend(records)

            if len(records) < current_limit:
                break

            offset += current_limit

        return collected[:desired_total]

    def get_record(self, app_id: str, record_id: str) -> Mapping[str, Any]:
        return self._request(
            "GET",
            "/k/v1/record.json",
            payload={"app": app_id, "id": record_id},
        )

    def _compose_query(self, user_query: str | None, limit: int, offset: int) -> str:
        base = (user_query or "").strip()
        lower = base.lower()
        if " limit " in lower or lower.endswith(" limit"):
            return base
        limit_clause = f"limit {limit} offset {offset}".strip()
        if base:
            return f"{base} {limit_clause}".strip()
        return limit_clause

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
            "kintone request | method=%s override=%s url=%s payload=%s headers=%s",
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


class DifyKbKintoneDatasourcePluginDataSource(OnlineDocumentDatasource):
    """Transforms a single kintone app into a Dify workspace."""

    DEFAULT_LIMIT = 100

    def _get_pages(self, datasource_parameters: dict[str, Any]) -> DatasourceGetPagesResponse:
        logger.error(
            "Datasource parameters snapshot: %r (type=%s)",
            datasource_parameters,
            type(datasource_parameters).__name__,
        )
        runtime_params = getattr(self.runtime, "parameters", None)
        logger.error(
            "Runtime parameters snapshot: %r (type=%s)",
            runtime_params,
            type(runtime_params).__name__ if runtime_params is not None else "None",
        )
        session_snapshot = {
            "app_id": getattr(self.session, "app_id", None),
            "endpoint_id": getattr(self.session, "endpoint_id", None),
            "conversation_id": getattr(self.session, "conversation_id", None),
            "message_id": getattr(self.session, "message_id", None),
            "install_method": getattr(self.session, "install_method", None),
            "context": getattr(self.session, "context", None),
        }
        logger.error(
            "Session snapshot (selected fields): %r",
            session_snapshot,
        )
        app_id = self._resolve_app_id(datasource_parameters)
        title_field = self._optional_parameter(datasource_parameters, "title_field")
        query = self._optional_parameter(datasource_parameters, "query")
        max_records = self._sanitize_record_limit(datasource_parameters.get("max_records"))
        debug_enabled = self._is_debug_enabled(datasource_parameters)
        self._debug_log(
            debug_enabled,
            "Starting _get_pages",
            app_id=app_id,
            query=query,
            max_records=max_records,
        )

        client = self._build_client()
        try:
            app_payload = client.get_app(app_id)
            workspace_name = app_payload.get("name") or f"App {app_id}"
            records = client.iter_records(app_id, query, max_records)
        except KintoneAPIError as exc:
            logger.exception("Failed to fetch kintone pages")
            raise ValueError(str(exc)) from exc
        self._debug_log(
            debug_enabled,
            "Fetched records",
            fetched=len(records),
            workspace_name=workspace_name,
        )
        pages = [
            OnlineDocumentPage(
                page_name=self._resolve_page_name(record, title_field),
                page_id=self._extract_record_id(record),
                type="page",
                last_edited_time=self._extract_last_edit(record),
                parent_id="",
                page_icon=None,
            )
            for record in records
            if self._extract_record_id(record)
        ]

        online_document_info = OnlineDocumentInfo(
            workspace_name=workspace_name,
            workspace_icon="",
            workspace_id=str(app_id),
            pages=pages,
            total=len(pages),
        )
        self._debug_log(debug_enabled, "Returning pages", page_count=len(pages))
        return DatasourceGetPagesResponse(result=[online_document_info])

    def _get_content(
        self, page: GetOnlineDocumentPageContentRequest
    ) -> Generator[DatasourceMessage, None, None]:
        app_id = page.workspace_id or self._optional_parameter(
            page.datasource_parameters or {},
            "app_id",
            aliases=("workspace_id",),
        )
        if not app_id:
            app_id = self._require_credential("app_id")
        record_id = page.page_id
        if not app_id or not record_id:
            raise ValueError("workspace_id (app id) and page_id (record id) are required to load content.")
        debug_enabled = self._is_debug_enabled(page.datasource_parameters)
        self._debug_log(
            debug_enabled,
            "Starting _get_content",
            app_id=app_id,
            record_id=record_id,
        )

        client = self._build_client()
        try:
            payload = client.get_record(str(app_id), str(record_id))
        except KintoneAPIError as exc:
            logger.exception("Failed to fetch kintone record", extra={"record_id": record_id})
            raise ValueError(str(exc)) from exc
        record = payload.get("record")
        if not isinstance(record, Mapping):
            raise ValueError("kintone record payload is missing from response.")

        content = KintoneRecordFormatter.render(record)
        self._debug_log(
            debug_enabled,
            "Rendered record content",
            record_id=record_id,
            content_length=len(content),
        )
        yield self.create_variable_message("content", content)
        yield self.create_variable_message("page_id", str(record_id))
        yield self.create_variable_message("workspace_id", str(app_id))

    # Helpers -----------------------------------------------------------------

    def _build_client(self) -> KintoneClient:
        base_url = self._require_credential("base_url")
        api_token = self._require_credential("api_token")
        return KintoneClient(base_url=base_url, api_token=api_token)

    def _require_parameter(self, parameters: Mapping[str, Any], key: str, *, aliases: tuple[str, ...] = ()) -> str:
        value = self._extract_parameter(parameters, key, aliases=aliases)
        if value is None:
            runtime_params = getattr(self.runtime, "parameters", None)
            if isinstance(runtime_params, Mapping):
                value = self._extract_parameter(runtime_params, key, aliases=aliases)
        if value is None:
            try:
                keys = sorted(parameters.keys()) if isinstance(parameters, Mapping) else "n/a"
            except Exception:  # pragma: no cover - defensive
                keys = "unavailable"
            try:
                runtime_keys = (
                    sorted(runtime_params.keys())
                    if isinstance(runtime_params, Mapping)
                    else "n/a"
                )
            except Exception:
                runtime_keys = "unavailable"
            credentials_snapshot: Mapping[str, Any] | None = getattr(self.runtime, "credentials", None)
            logger.error(
                (
                    "Required parameter missing or empty: key=%s aliases=%s "
                    "keys=%s runtime_keys=%s payload=%r runtime=%r credentials=%r"
                ),
                key,
                aliases,
                keys,
                runtime_keys,
                parameters,
                runtime_params,
                credentials_snapshot,
            )
            raise ValueError(
                "Parameter '{key}' is required. "
                "Provided keys: {keys}. Runtime keys: {runtime_keys}. "
                "Payload snapshot: {payload}. Runtime snapshot: {runtime}. Credentials snapshot: {creds}".format(
                    key=key,
                    keys=keys,
                    runtime_keys=runtime_keys,
                    payload=parameters,
                    runtime=runtime_params,
                    creds=credentials_snapshot,
                )
            )
        return value

    def _optional_parameter(
        self,
        parameters: Mapping[str, Any],
        key: str,
        *,
        aliases: tuple[str, ...] = (),
    ) -> str | None:
        return self._extract_parameter(parameters, key, aliases=aliases)

    def _require_credential(self, key: str) -> str:
        value = str(self.runtime.credentials.get(key) or "").strip()
        if not value:
            raise ValueError(f"Credential '{key}' is missing.")
        return value

    def _resolve_app_id(self, parameters: Mapping[str, Any]) -> str:
        app_id = self._extract_parameter(parameters, "app_id", aliases=("workspace_id",))
        if app_id:
            return app_id
        return self._require_credential("app_id")

    def _sanitize_record_limit(self, raw_limit: Any) -> int:
        try:
            limit = int(raw_limit or self.DEFAULT_LIMIT)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_records must be an integer") from exc
        return max(1, min(limit, KintoneClient.MAX_CHUNK))

    def _is_debug_enabled(self, parameters: Mapping[str, Any] | None) -> bool:
        value: Any | None = None
        if parameters:
            value = parameters.get("debug_logging")
        if value is None:
            runtime_params = getattr(self.runtime, "parameters", None)
            if isinstance(runtime_params, Mapping):
                value = runtime_params.get("debug_logging")
        return bool(value)

    def _debug_log(self, enabled: bool, message: str, **extra: Any) -> None:
        if not enabled:
            return
        if extra:
            logger.info("[debug] %s | extra=%s", message, extra)
        else:
            logger.info("[debug] %s", message)

    @staticmethod
    def _candidate_keys(key: str, aliases: tuple[str, ...]) -> list[str]:
        candidates = [key, *aliases]
        camel = "".join(
            [parts[0]] + [part.capitalize() for part in parts[1:]]
        ) if (parts := key.split("_")) else key
        if camel and camel not in candidates:
            candidates.append(camel)
        hyphenated = key.replace("_", "-")
        if hyphenated not in candidates:
            candidates.append(hyphenated)
        return candidates

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
    def _extract_record_id(record: Mapping[str, Any]) -> str:
        payload = record.get("$id")
        if isinstance(payload, Mapping) and payload.get("value") is not None:
            return str(payload["value"])
        return ""

    def _resolve_page_name(
        self,
        record: Mapping[str, Any],
        title_field: str | None,
    ) -> str:
        if title_field:
            field_payload = record.get(title_field)
            if isinstance(field_payload, Mapping):
                field_value = field_payload.get("value")
                if field_value:
                    return str(field_value)
        record_id = self._extract_record_id(record)
        return record_id or "kintone record"

    @staticmethod
    def _extract_last_edit(record: Mapping[str, Any]) -> str:
        timestamp_candidates = ("更新日時", "Updated_datetime", "_updated_at")
        for field in timestamp_candidates:
            payload = record.get(field)
            if isinstance(payload, Mapping) and payload.get("value"):
                return str(payload["value"])
        return ""
