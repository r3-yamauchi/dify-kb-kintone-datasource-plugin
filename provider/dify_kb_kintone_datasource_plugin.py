# where: provider/dify_kb_kintone_datasource_plugin.py
# what: Validates the base URL and API token used by the kintone datasource provider.
# why: Ensures the plugin only runs with well-formed credentials before hitting the datasource logic.

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from dify_plugin.errors.tool import DatasourceCredentialValidationError
from dify_plugin.interfaces.datasource import DatasourceProvider


class DifyKbKintoneDatasourcePluginProvider(DatasourceProvider):
    """Basic provider that validates static kintone credentials."""

    def _validate_credentials(self, credentials: Mapping[str, Any]) -> None:
        base_url = str(credentials.get("base_url") or "").strip()
        api_token = str(credentials.get("api_token") or "").strip()
        app_id = str(credentials.get("app_id") or "").strip()

        if not base_url:
            raise DatasourceCredentialValidationError("kintone base URL is required.")

        parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
        if not parsed.scheme or not parsed.netloc:
            raise DatasourceCredentialValidationError(
                "kintone base URL must include a hostname (e.g. https://example.cybozu.com)."
            )

        if parsed.scheme != "https":
            raise DatasourceCredentialValidationError("kintone base URL must use HTTPS.")

        if not api_token:
            raise DatasourceCredentialValidationError("kintone API token is required.")

        if not app_id:
            raise DatasourceCredentialValidationError("kintone app ID is required.")
        if not app_id.isdigit():
            raise DatasourceCredentialValidationError("kintone app ID must be a numeric value.")
