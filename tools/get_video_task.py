from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from mh_client import ModelHubClient, ModelHubError, check_task_id
from tools._common import client_for, deliver_video, summary


class GetVideoTaskTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        client = client_for(self)
        try:
            task_id = check_task_id(tool_parameters.get("task_id") or "")
            try:
                state = client.get_task(task_id)
            except ModelHubError:
                raise
            except Exception as exc:  # network errors from requests
                raise ModelHubError(f"Could not reach ModelHub ({type(exc).__name__}). Try again.") from None
            state.setdefault("task_id", task_id)
            status = state["status"]
            if status == "failed":
                raise ModelHubError(
                    f"Task {task_id} failed: {ModelHubClient.failure_reason(state)}. Failed tasks are not charged."
                )
            if status == "cancelled":
                raise ModelHubError(f"Task {task_id} was cancelled: {ModelHubClient.cancel_reason(state)}.")
            if status != "completed":
                yield self.create_json_message(summary(state))
                yield self.create_text_message(f"Task {task_id} is still {status}. Check again in a minute.")
                return
            if not ModelHubClient.video_url(state):
                raise ModelHubError(f"Task {task_id} completed without a video link.")
            yield from deliver_video(self, client, state)
        except ModelHubError as exc:
            raise ValueError(str(exc)) from None
