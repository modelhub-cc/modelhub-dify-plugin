from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from mh_client import ModelHubError, TaskStillRunning, build_payload, media_file
from tools._common import as_list, client_for, deliver_video, read_blob, split_urls

KINDS = (("image", "reference_images", "reference_image_urls"),
         ("video", "reference_videos", "reference_video_urls"),
         ("audio", "reference_audios", "reference_audio_urls"))


def collect_media(params: dict[str, Any]) -> tuple[dict[str, list[Any]], bool]:
    """Return ({"images", "videos", "audios"}, first_last_frames) from the tool parameters.

    Uploaded files come first, then links, numbered in that order.
    """
    first = params.get("first_frame")
    last = params.get("last_frame")
    media: dict[str, list[Any]] = {}
    for kind, files_param, urls_param in KINDS:
        uploads = as_list(params.get(files_param))
        items: list[Any] = [
            media_file(read_blob(upload, f"reference {kind} {index}"), kind, index)
            for index, upload in enumerate(uploads, 1)
        ]
        items.extend(split_urls(params.get(urls_param)))
        media[kind + "s"] = items

    if first or last:
        if any(media.values()):
            raise ModelHubError("Use either first/last frames or reference images, videos and audio, not both.")
        if not first:
            raise ModelHubError("A last frame needs a first frame too.")
        frames = [media_file(read_blob(first, "first frame"), "image", 1)]
        if last:
            frames.append(media_file(read_blob(last, "last frame"), "image", 2))
        return {"images": frames, "videos": [], "audios": []}, True
    return media, False


class GenerateVideoTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        client = client_for(self)
        try:
            media, frames_mode = collect_media(tool_parameters)
            generate_audio = tool_parameters.get("generate_audio")
            payload = build_payload(
                tool_parameters.get("model") or "seedance-2.0-mini",
                tool_parameters.get("prompt") or "",
                duration=tool_parameters.get("duration") or 5,
                resolution=tool_parameters.get("resolution") or "720p",
                ratio=tool_parameters.get("ratio") or "16:9",
                generate_audio=None if generate_audio is None else bool(generate_audio),
                first_last_frames=frames_mode,
                **media,
            )
            submitted = client.submit(payload)
            task_id = submitted["task_id"]
            wait = tool_parameters.get("wait_for_result")
            if wait is not None and not wait:
                yield self.create_json_message(
                    {
                        "task_id": task_id,
                        "status": submitted.get("status") or "processing",
                        "model": submitted.get("effective_model") or payload["model"],
                    }
                )
                yield self.create_text_message(
                    f"The video task was submitted. Task ID: {task_id}. Use Get Video Task to fetch the result."
                )
                return
            try:
                state = client.wait(task_id)
            except TaskStillRunning as running:
                yield self.create_json_message({"task_id": task_id, "status": running.state.get("status")})
                yield self.create_text_message(
                    f"The video is still being made. Task ID: {task_id}. "
                    "Use Get Video Task in a few minutes to fetch it."
                )
                return
            state.setdefault("task_id", task_id)
            state.setdefault("effective_model", submitted.get("effective_model"))
            yield from deliver_video(self, client, state)
        except ModelHubError as exc:
            raise ValueError(str(exc)) from None
