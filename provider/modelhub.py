from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from mh_client import ModelHubClient, ModelHubError


class ModelHubProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        # Queries a task ID that does not exist: a bad key gets 401, a good key 404.
        # No job is started and nothing is charged.
        try:
            ModelHubClient(credentials.get("api_key") or "").validate_key()
        except ModelHubError as exc:
            raise ToolProviderCredentialValidationError(str(exc)) from None
