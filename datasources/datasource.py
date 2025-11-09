"""kintone の添付ファイルを Dify から参照できるオンラインドライブ型データソース。"""

# where: datasources/datasource.py
# what: Dify の OnlineDriveDatasource 経由で kintone 添付ファイルを列挙・ダウンロードする実装。
# why: kintone に保存されたファイルを複製せず参照し、Dify から直接取得できるようにするため。

from __future__ import annotations

import json
import logging
import os
import re
import time
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


class KintoneAPIError(RuntimeError):
    """kintone REST API 呼び出しでエラーが返った場合に送出される例外。"""


class KintoneClient:
    """データソースが利用する最小構成の kintone REST API クライアント。"""

    MAX_CHUNK: int = 500  # kintone REST API が 1 回で返せるレコード数の上限
    RETRY_ATTEMPTS: int = 3
    RETRY_BACKOFF_SECONDS: float = 1.0

    def __init__(self, base_url: str, api_token: str, timeout: int = 30) -> None:
        self.api_token = api_token
        self.timeout = timeout

        cleaned = base_url.rstrip("/")
        if "://" not in cleaned:
            cleaned = f"https://{cleaned}"
        self.base_url = cleaned.rstrip("/")

    def fetch_records(
        self,
        app_id: str,
        query: str,
        *,
        fields: Iterable[str] | None = None,
    ) -> list[Mapping[str, Any]]:
        # NOTE: kintone 仕様に合わせて limit/offset を文字列クエリに埋め込むため、ここでは追加パラメータを構築するだけ
        payload: dict[str, Any] = {
            "app": app_id,
            "query": query,
        }
        if fields:
            payload["fields"] = list(fields)

        response_payload = self._request(
            "GET",
            "/k/v1/records.json",
            payload=payload,
        )

        records = response_payload.get("records", []) if isinstance(response_payload, Mapping) else []
        return records

    def get_record(self, app_id: str, record_id: str) -> Mapping[str, Any]:
        # NOTE: 単一レコード取得は kintone API 側で ID を指定するだけなので、そのまま委譲する
        return self._request(
            "GET",
            "/k/v1/record.json",
            payload={"app": app_id, "id": record_id},
        )

    def download_file(self, file_key: str) -> bytes:
        """fileKey を指定して添付ファイルをダウンロードし、必要に応じてリトライする。"""

        url = urljoin(self.base_url + "/", "k/v1/file.json")
        query = urlencode({"fileKey": file_key})
        request = Request(
            url=f"{url}?{query}",
            method="GET",
            headers={
                "X-Cybozu-API-Token": self.api_token,
            },
        )

        # NOTE: 「kintone download ...」という英語ログは添付ダウンロード開始を示す。日本語では「kintone からのダウンロード要求開始」を意味する。
        logger.info("kintone download | file_key=%s", self._mask_secret(file_key))

        for attempt in range(self.RETRY_ATTEMPTS):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except HTTPError as exc:
                # NOTE: 「kintone download failed ...」は HTTP エラーで再試行する旨を知らせるログ。
                logger.warning(
                    "kintone download failed (attempt %s/%s) with HTTP %s",
                    attempt + 1,
                    self.RETRY_ATTEMPTS,
                    getattr(exc, "code", "unknown"),
                )
                if self._should_retry_http(exc, attempt):
                    self._sleep_before_retry(attempt)
                    continue
                error_body = exc.read().decode("utf-8", "ignore")
                # NOTE: エラーメッセージは「HTTP エラーで添付取得に失敗」を意味する。
                raise KintoneAPIError(
                    f"kintone file download failed with {exc.code}: {error_body or exc.reason}"
                ) from exc
            except URLError as exc:  # pragma: no cover - ネットワーク障害は上位へそのまま伝播させる
                # NOTE: 「network error」系ログは kintone へ接続できなかったことを通知している。
                logger.warning(
                    "kintone download failed (attempt %s/%s) with network error: %s",
                    attempt + 1,
                    self.RETRY_ATTEMPTS,
                    exc.reason,
                )
                if self._should_retry_network(attempt):
                    self._sleep_before_retry(attempt)
                    continue
                # NOTE: 「Failed to reach kintone ...」は kintone に到達できず失敗したことを示す日本語訳が必要。
                raise KintoneAPIError(f"Failed to reach kintone for file download: {exc.reason}") from exc

        # NOTE: 「failed after retries」は「規定回数の再試行後も失敗」を意味する。
        raise KintoneAPIError("kintone file download failed after retries.")

    @staticmethod
    def _mask_secret(secret: str) -> str:
        if not secret:
            return "***"
        if len(secret) <= 6:
            return "***"
        return f"{secret[:3]}***{secret[-3:]}"

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
        # NOTE: このログは kintone API への HTTP 呼び出し内容を記録している。
        logger.info(
            "kintone request | method=%s override=%s path=%s payload=%s headers=%s",
            "POST",
            method.upper(),
            path,
            payload,
            masked_headers,
        )

        for attempt in range(self.RETRY_ATTEMPTS):
            # NOTE: HTTP ステータスやネットワーク例外に応じて最大 RETRY_ATTEMPTS 回まで再試行
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = response.read().decode("utf-8")
                return json.loads(payload or "{}")
            except HTTPError as exc:
                # NOTE: 「kintone request failed ...」は HTTP レスポンスがエラーとなったことを示す。
                logger.warning(
                    "kintone request failed (attempt %s/%s) with HTTP %s",
                    attempt + 1,
                    self.RETRY_ATTEMPTS,
                    getattr(exc, "code", "unknown"),
                )
                if self._should_retry_http(exc, attempt):
                    self._sleep_before_retry(attempt)
                    continue
                error_body = exc.read().decode("utf-8", "ignore")
                # NOTE: 「kintone API error ...」は API からエラー応答が返ったことを示す。
                raise KintoneAPIError(
                    f"kintone API error {exc.code} for {path}: {error_body or exc.reason}"
                ) from exc
            except URLError as exc:  # pragma: no cover - ネットワーク障害は上位へそのまま伝播させる
                # NOTE: ネットワーク到達不能を通知するログ。
                logger.warning(
                    "kintone request failed (attempt %s/%s) with network error: %s",
                    attempt + 1,
                    self.RETRY_ATTEMPTS,
                    exc.reason,
                )
                if self._should_retry_network(attempt):
                    self._sleep_before_retry(attempt)
                    continue
                # NOTE: 「Failed to reach kintone」は接続不能の意。
                raise KintoneAPIError(f"Failed to reach kintone: {exc.reason}") from exc

        # NOTE: 「API error after retries」は規定回数の再試行後に失敗し続けたことを示す。
        raise KintoneAPIError("kintone API error after retries.")

    def _should_retry_http(self, exc: HTTPError, attempt: int) -> bool:
        return 500 <= getattr(exc, "code", 0) < 600 and attempt + 1 < self.RETRY_ATTEMPTS

    def _should_retry_network(self, attempt: int) -> bool:
        return attempt + 1 < self.RETRY_ATTEMPTS

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = self.RETRY_BACKOFF_SECONDS * (attempt + 1)
        time.sleep(delay)


class DifyKbKintoneDatasourcePluginDataSource(OnlineDriveDatasource):
    """kintone 添付ファイルを Dify から参照できる仮想ドライブとして変換する本体クラス。"""

    DEFAULT_LIMIT = 500

    def _browse_files(self, request: OnlineDriveBrowseFilesRequest) -> OnlineDriveBrowseFilesResponse:
        # NOTE: Dify ランタイムから渡される資格情報のみを信頼し、datasource パラメータは debug フラグ以外使わない
        # NOTE: プラグイン設定（provider credentials + debug flag）のスナップショットを取得
        config = self._gather_configuration()
        app_id = self._resolve_app_id(config, request.bucket)
        query = self._optional_parameter(config, "query") or ""
        attachment_codes = self._parse_attachment_field_codes(
            self._require_parameter(config, "attachment_field_codes", aliases=("attachmentFields",))
        )
        if not attachment_codes:
            raise ValueError("attachment_field_codes must contain at least one field code.")

        # NOTE: limit/offset をクエリから剥がして制御しつつ、ORDER BY が無ければ $id 昇順を強制する
        raw_query, user_limit, user_offset = self._normalize_query(query)
        base_query = self._ensure_order_clause(raw_query)
        paginate = user_limit is None
        request_limit = user_limit if user_limit is not None else KintoneClient.MAX_CHUNK
        offset = user_offset if user_offset is not None else 0
        max_files = self._determine_page_size(request.max_keys, self.DEFAULT_LIMIT)
        offset_from_request, attachment_cursor = self._parse_pagination_state(request.next_page_parameters)
        if request.next_page_parameters is not None:
            offset = offset_from_request
        debug_enabled = self._is_debug_enabled(config)

        bucket_id = self._compose_bucket_identifier(config, app_id)
        self._debug_log(
            debug_enabled,
            "Starting _browse_files",
            app_id=app_id,
            query=base_query,
            attachment_fields=attachment_codes,
            user_limit=user_limit,
            max_files=max_files,
            offset=offset,
            attachment_cursor=attachment_cursor,
            bucket_id=bucket_id,
        )

        client = self._build_client(config)
        files: list[OnlineDriveFile] = []  # 今回の browse 呼び出しでユーザーに返却するファイル一覧
        next_offset = offset
        next_attachment_cursor = 0
        has_more = False
        current_attachment_cursor = attachment_cursor

        fields = self._build_field_selection(attachment_codes)  # 添付フィールドと $id のみ取得してレスポンス量を抑える

        stop_processing = False  # True になるとループを即座に抜ける（max_files 到達など）
        records: list[Mapping[str, Any]] = []
        while len(files) < max_files and not stop_processing:
            # NOTE: kintone API は limit/offset 句を直接クエリに指定する必要があるため、1 バッチ毎に文字列を生成
            paged_query = self._compose_paginated_query(base_query, request_limit, offset)
            try:
                records = client.fetch_records(app_id, paged_query, fields=fields)
            except KintoneAPIError as exc:
                # NOTE: 「Failed to fetch kintone records ...」はレコード取得に失敗した旨を記録する。
                logger.exception("Failed to fetch kintone records during browse_files")
                raise ValueError(str(exc)) from exc

            if not records:
                break

            batch_base_offset = offset
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
                            or (paginate and len(records) == request_limit)
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

            offset = batch_base_offset + len(records)

            if not paginate:
                # NOTE: ユーザーが limit を明示したら1バッチで終える（kintone_query.py と同じ挙動）
                break
            if len(records) < request_limit:
                break

        if not stop_processing and len(files) < max_files:
            has_more = False
            next_offset = offset
            next_attachment_cursor = 0

        next_page_parameters = None  # has_more の場合にクライアントへ渡す次ページ用カーソル
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
        # NOTE: browse 時と同じ資格情報を再利用し、アプリ/添付フィールド設定を決定
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
            # NOTE: 「Failed to fetch kintone record for download」はダウンロード前のレコード取得失敗を意味する。
            logger.exception("Failed to fetch kintone record for download", extra={"record_id": record_id})
            raise ValueError(str(exc)) from exc

        record = payload.get("record")  # kintone API は record キー配下に本体を返す
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
            # NOTE: 「Failed to download kintone attachment」は添付ダウンロード失敗を示している。
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

    # 補助メソッド ------------------------------------------------------------

    def _build_client(self, parameters: Mapping[str, Any]) -> KintoneClient:
        # NOTE: provider 側で必須入力済みの base_url / api_token を解決してクライアントを生成
        base_url = self._resolve_kintone_base_url(parameters)
        api_token = self._resolve_kintone_api_token(parameters)
        return KintoneClient(base_url=base_url, api_token=api_token)

    def _gather_configuration(self) -> Mapping[str, Any]:
        runtime_state = {
            "runtime_type": type(self.runtime).__name__,
            "has_credentials": hasattr(self.runtime, "credentials"),
            "has_parameters": hasattr(self.runtime, "parameters"),
        }
        # NOTE: 「Runtime state snapshot」はランタイムの状態をデバッグ出力するログ。
        logger.debug("Runtime state snapshot: %s", runtime_state)

        combined: dict[str, Any] = {}
        # NOTE: 信頼できるのは provider 側の資格情報なので、まず credentials をコピーする
        credentials = self._ensure_mapping(getattr(self.runtime, "credentials", {}))
        combined.update(credentials)

        runtime_params = getattr(self.runtime, "parameters", None)
        if isinstance(runtime_params, Mapping):
            # NOTE: datasource パラメータでは debug フラグのみ許容する
            debug_override = runtime_params.get("debug_logging")
            if debug_override is not None:
                combined["debug_logging"] = debug_override
        else:
            # NOTE: 「runtime.parameters is unavailable ...」は datasource パラメータが無いことを示す。
            logger.debug("runtime.parameters is unavailable or not a mapping: %r", runtime_params)

        return combined

    @staticmethod
    def _ensure_mapping(candidate: Any) -> Mapping[str, Any]:
        return candidate if isinstance(candidate, Mapping) else {}

    def _resolve_app_id(self, parameters: Mapping[str, Any], bucket: str | None) -> str:
        configured = self._require_parameter(parameters, "app_id", aliases=("workspace_id", "bucket"))
        if bucket:
            bucket_str = str(bucket).strip()
            if bucket_str and bucket_str != configured:
                raise ValueError(
                    "Bucket identifier does not match the configured app_id; per-datasource overrides are not supported."
                )
        return configured

    def _require_parameter(self, parameters: Mapping[str, Any], key: str, *, aliases: tuple[str, ...] = ()) -> str:
        value = self._extract_parameter(parameters, key, aliases=aliases)
        if value is None:
            # NOTE: 「Required parameter missing ...」は必須パラメータ欠如を明示する。
            logger.error(
                "Required parameter missing: key=%s aliases=%s parameters=%r",
                key,
                aliases,
                parameters,
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
        # NOTE: 最大 9 個まで許容される API トークンを改行区切りへ変換し、kintone API の仕様に合わせる
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
        # NOTE: kintone ドメインを正規化した値でバケット名を作り、複数アプリを区別する
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

    def _determine_page_size(self, requested: int | None, fallback: int) -> int:
        try:
            if requested is None:
                return fallback
            return max(1, int(requested))
        except (TypeError, ValueError):
            return fallback

    def _is_debug_enabled(self, parameters: Mapping[str, Any] | None) -> bool:
        if not parameters:
            return False
        # NOTE: debug_logging だけは datasource パラメータでオンオフできるよう緩めている
        return bool(parameters.get("debug_logging"))

    def _parse_attachment_field_codes(self, raw_codes: str) -> list[str]:
        # NOTE: provider 設定で指定されたカンマ区切りフィールドコードを順序維持で正規化
        if not raw_codes:
            return []
        codes = []
        for code in raw_codes.split(","):
            cleaned = code.strip()
            if cleaned and cleaned not in codes:
                codes.append(cleaned)
        return codes

    @staticmethod
    def _build_field_selection(attachment_codes: Iterable[str]) -> list[str]:
        # NOTE: API レスポンスを最小限に抑えるため、添付フィールドと $id のみ取得する
        fields: list[str] = []
        seen: set[str] = set()
        for candidate in ["$id", *attachment_codes]:
            if candidate and candidate not in seen:
                fields.append(candidate)
                seen.add(candidate)
        return fields

    @staticmethod
    def _parse_pagination_state(next_page: Mapping[str, Any] | None) -> tuple[int, int]:
        # NOTE: Dify から渡された next_page_parameters を offset / attachment_cursor に分解
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
        # NOTE: snake_case / camelCase / hyphen-case を許容してユースケースの揺れを吸収
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
    def _ensure_order_clause(query: str) -> str:
        # NOTE: ORDER BY が無いと kintone の返却順が安定しないため、明示的に $id 昇順を付与して決定論的なページングを実現
        normalized = query.lower()
        if "order by" in normalized:
            return query.strip()
        cleaned = query.strip()
        if cleaned:
            return f"{cleaned} order by $id asc"
        return "order by $id asc"

    @staticmethod
    def _compose_paginated_query(base_query: str, limit: int, offset: int) -> str:
        # NOTE: kintone は limit/offset をHTTPボディの query 文字列へ記述する仕様
        clauses = []
        cleaned = base_query.strip()
        if cleaned:
            clauses.append(cleaned)
        clauses.append(f"limit {limit}")
        clauses.append(f"offset {offset}")
        return " ".join(filter(None, clauses)).strip()

    @staticmethod
    def _remove_trailing_connectors(query: str) -> str:
        cleaned = query
        while True:
            stripped = re.sub(r"\s+", " ", cleaned).strip()
            if not stripped:
                return ""
            if re.search(r"(and|or)$", stripped, flags=re.IGNORECASE):
                cleaned = re.sub(r"(and|or)\s*$", "", stripped, flags=re.IGNORECASE)
                continue
            return stripped

    def _normalize_query(self, raw_query: str) -> tuple[str, int | None, int | None]:
        query = (raw_query or "").strip()
        if not query:
            return "", None, None

        normalized = re.sub(r"\s+", " ", query)
        tokens = normalized.split(" ")
        lower_tokens = [token.lower() for token in tokens]
        if lower_tokens.count("limit") > 1 or lower_tokens.count("offset") > 1:
            raise ValueError("Query may contain at most one limit and one offset clause.")

        # NOTE: kintone_query.py と同様に limit/offset を取り除き、我々側でページネーションを制御する
        limit_match = re.search(r"\blimit\s+(\d+)", query, flags=re.IGNORECASE)
        user_limit: int | None = None
        if limit_match:
            user_limit = int(limit_match.group(1))
            if user_limit <= 0:
                raise ValueError("Query limit must be a positive integer.")
            if user_limit > KintoneClient.MAX_CHUNK:
                raise ValueError(
                    f"Query limit cannot exceed {KintoneClient.MAX_CHUNK}; omit the clause to fetch all records."
                )
            query = re.sub(r"\blimit\s+\d+", "", query, flags=re.IGNORECASE)

        offset_match = re.search(r"\boffset\s+(\d+)", query, flags=re.IGNORECASE)
        user_offset: int | None = None
        if offset_match:
            user_offset = int(offset_match.group(1))
            if user_offset < 0:
                raise ValueError("Query offset must be zero or a positive integer.")
            query = re.sub(r"\boffset\s+\d+", "", query, flags=re.IGNORECASE)

        query = self._remove_trailing_connectors(query)
        return query, user_limit, user_offset

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
        # NOTE: 添付フィールド全体を走査し、fileKey が一致するエントリを返す
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
            # NOTE: デバッグログ「[debug] ... extra=...」は付加情報つきで状況を記録する。
            logger.info("[debug] %s | extra=%s", message, extra)
        else:
            # NOTE: 追加情報の無いデバッグログ。
            logger.info("[debug] %s", message)
