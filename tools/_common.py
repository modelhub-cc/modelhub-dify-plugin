"""Behaviour shared by the tools (the SDK allows one Tool subclass per source file)."""

from __future__ import annotations

import re
from collections.abc import Generator
from typing import Any

from mh_client import MAX_VIDEO_BYTES, ModelHubClient, ModelHubError, VideoTooLarge


def client_for(tool: Any) -> ModelHubClient:
    return ModelHubClient(tool.runtime.credentials.get("api_key") or "")


def as_list(value: Any) -> list[Any]:
    if not value:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def split_urls(text: Any) -> list[str]:
    if not text:
        return []
    return [part for part in re.split(r"[\s,]+", str(text)) if part]


def read_blob(upload: Any, what: str) -> bytes:
    try:
        return upload.blob
    except Exception as exc:  # the SDK raises httpx or ValueError errors here
        raise ModelHubError(f"Could not read the {what} from Dify ({type(exc).__name__}).") from None


def summary(state: dict[str, Any]) -> dict[str, Any]:
    out = {
        "task_id": state.get("task_id"),
        "status": state.get("status"),
        "model": state.get("effective_model") or state.get("model") or state.get("requested_model"),
        "progress": state.get("progress"),
        "credits_consumed": ModelHubClient.credits(state),
        "video_url": ModelHubClient.video_url(state),
    }
    return {k: v for k, v in out.items() if v is not None}


def deliver_video(tool: Any, client: ModelHubClient, state: dict[str, Any]) -> Generator[Any, None, None]:
    task_id = str(state.get("task_id") or "")
    info = summary(state)
    yield tool.create_json_message(info)
    url = ModelHubClient.video_url(state) or ""
    credits = info.get("credits_consumed")
    cost = f" Credits used: {credits}." if credits is not None else ""
    try:
        data, mime = client.download(url)
    except VideoTooLarge:
        limit = MAX_VIDEO_BYTES // (1024 * 1024)
        yield tool.create_text_message(
            f"The video is ready (task {task_id}) but larger than {limit} MB, so it is not attached. "
            f"Download it from this temporary link soon: {url}{cost}"
        )
        return
    yield tool.create_text_message(f"The video is ready (task {task_id}).{cost}")
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id) or "video"
    yield tool.create_blob_message(blob=data, meta={"mime_type": mime, "filename": f"modelhub-{safe_id}.mp4"})
