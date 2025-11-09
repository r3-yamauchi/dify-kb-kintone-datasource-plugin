# where: provider/provider.py
# what: Validates the base URL and API token used by the kintone datasource provider.
# why: Ensures the plugin only runs with well-formed credentials before hitting the datasource logic.

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from dify_plugin.errors.tool import DatasourceCredentialValidationError
from dify_plugin.interfaces.datasource import DatasourceProvider


class DifyKbKintoneDatasourcePluginProvider(DatasourceProvider):
    """Validates shared kintone credentials used by the online drive datasource."""

    def _validate_credentials(self, credentials: Mapping[str, Any]) -> None:
        base_url = str(credentials.get("kintone_base_url") or "").strip()
        raw_tokens = str(credentials.get("kintone_api_token") or "").strip()
        raw_app_id = credentials.get("app_id")

        if base_url:
            parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
            if not parsed.scheme or not parsed.netloc:
                raise DatasourceCredentialValidationError(
                    "kintone base URL must include a hostname (e.g. https://example.cybozu.com)."
                )

            if parsed.scheme != "https":
                raise DatasourceCredentialValidationError("kintone base URL must use HTTPS.")

        if raw_tokens:
            tokens = [token.strip() for token in raw_tokens.split(",") if token and token.strip()]
            if len(tokens) > 9:
                raise DatasourceCredentialValidationError("APIトークンは最大9個まで入力できます。")

        if raw_app_id not in (None, ""):
            try:
                value = int(str(raw_app_id).strip())
            except ValueError as exc:
                raise DatasourceCredentialValidationError("app_id には数値を入力してください。") from exc
            if value <= 0:
                raise DatasourceCredentialValidationError("app_id には正の整数を入力してください。")
