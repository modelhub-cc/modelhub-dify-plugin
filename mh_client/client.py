"""Small client for the ModelHub video API (https://modelhub.cc/v1).

API behaviour this client relies on (checked against the live service):

* ``POST /v1/videos/generations`` takes JSON, or multipart/form-data when local
  images are attached as ``image_file_1..N``. The reply is a flat object:
  ``{created, task_id, status, requested_model, effective_model}``.
* ``GET /v1/videos/tasks/{task_id}`` returns ``{status, progress, creditsConsumed,
  result: {url}}``. There are four public states: ``processing`` (progress stays 0),
  ``completed`` (``result.url``), ``failed`` (reason in the top-level ``error``
  string, plus ``error_code`` and ``failureType``) and ``cancelled`` (top-level
  ``error``). ``failed`` and ``cancelled`` are both final. The public docs spell the
  credits field ``credits_consumed``; both spellings are read. An unknown task is
  HTTP 404 ``{"error": {"message", "code": "-2011"}}``.
* ``functionMode=first_last_frames`` takes exactly two images and no videos or
  audio; a first frame on its own is sent as ``omni_reference``.
* Errors are HTTP 4xx with ``{"error": {"message", "type", "code"}}``. A rejected
  request is not charged. ``data:`` URIs are rejected, so local images are always
  sent as multipart parts.
* ``GET /v1/videos/tasks/<unknown id>`` answers 401 for a bad key and 404 for a
  good one, which is how a key is checked without starting a paid job.

A submit that times out or gets a 5xx has an unknown outcome (the job may exist
and may be charged), so a submit is never retried automatically.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

API_BASE = "https://modelhub.cc/v1"

# model id -> (min seconds, max seconds)
MODELS: dict[str, tuple[int, int]] = {
    "seedance-2.0-mini": (4, 15),
    "seedance-2.0-fast": (4, 15),
    "seedance-2.0": (4, 15),
    "seedance-2.5": (4, 30),
    "seedance-2.5-flash": (4, 30),
}
# Omni-reference limits per model: (images, videos, audios)
REFERENCE_LIMITS: dict[str, tuple[int, int, int]] = {
    "seedance-2.0-mini": (9, 3, 3),
    "seedance-2.0-fast": (9, 3, 3),
    "seedance-2.0": (9, 3, 3),
    "seedance-2.5": (30, 10, 10),
    "seedance-2.5-flash": (10, 5, 5),
}
RESOLUTIONS = ("480p", "720p", "1080p")
MODEL_RESOLUTIONS = {"seedance-2.5-flash": ("480p", "720p")}
NO_AUDIO_OPTION = {"seedance-2.5-flash"}  # the model takes no generate_audio flag
RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")

MAX_REFERENCE_IMAGES = max(limit[0] for limit in REFERENCE_LIMITS.values())
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_REF_VIDEO_BYTES = 50 * 1024 * 1024
MAX_REF_AUDIO_BYTES = 15 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024  # Dify's default plugin file size limit

SHORT_TIMEOUT = (10, 30)
SUBMIT_TIMEOUT = (10, 120)
DOWNLOAD_TIMEOUT = (10, 120)
POLL_SECONDS = 6.0
WAIT_SECONDS = 480.0  # stays under the 600 s plugin request limit, with time left to download
MAX_REDIRECTS = 3
MAX_POLL_ERRORS = 5

# The video storage may refuse requests without a browser-like User-Agent.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 modelhub-dify-plugin/0.0.1"
)

TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

MediaFile = tuple[str, bytes, str]  # (filename, data, mime type)
MediaRef = str | MediaFile
ImageFile = MediaFile
ImageRef = MediaRef


class ModelHubError(Exception):
    """Base class. Messages are safe to show: they never contain the API key."""


class AuthError(ModelHubError):
    pass


class RequestError(ModelHubError):
    """ModelHub rejected the request (HTTP 4xx). Nothing was charged."""


class UnknownOutcome(ModelHubError):
    """The submit got no definite answer; the job may exist."""


class TaskFailed(ModelHubError):
    def __init__(self, message: str, task_id: str):
        super().__init__(message)
        self.task_id = task_id


class TaskStillRunning(ModelHubError):
    def __init__(self, task_id: str, state: dict[str, Any]):
        super().__init__(f"Task {task_id} is still running.")
        self.task_id = task_id
        self.state = state


class VideoTooLarge(ModelHubError):
    pass


def check_task_id(task_id: str) -> str:
    task_id = (task_id or "").strip()
    if not TASK_ID_RE.match(task_id):
        raise ModelHubError("The task ID is empty or has unexpected characters.")
    return task_id


def sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


IMAGE_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def sniff_video(data: bytes) -> str | None:
    if data[4:8] == b"ftyp":
        return "video/quicktime" if data[8:10] == b"qt" else "video/mp4"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    return None


def sniff_audio(data: bytes) -> str | None:
    if data.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[4:8] == b"ftyp":
        return "audio/mp4"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data.startswith(b"fLaC"):
        return "audio/flac"
    if data[:2] in (b"\xff\xf1", b"\xff\xf9"):
        return "audio/aac"
    return None


MEDIA_KINDS = {
    # kind: (sniffer, size limit, allowed formats text)
    "image": (sniff_image, MAX_IMAGE_BYTES, "JPEG, PNG or WebP"),
    "video": (sniff_video, MAX_REF_VIDEO_BYTES, "MP4, MOV or WebM"),
    "audio": (sniff_audio, MAX_REF_AUDIO_BYTES, "MP3, WAV, M4A, AAC, OGG or FLAC"),
}
EXTENSIONS = {
    **IMAGE_EXTENSIONS,
    "video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm",
    "audio/mpeg": "mp3", "audio/wav": "wav", "audio/mp4": "m4a", "audio/ogg": "ogg",
    "audio/flac": "flac", "audio/aac": "aac",
}


def media_file(data: bytes, kind: str, index: int) -> MediaFile:
    """Wrap raw bytes as a multipart part, checking type and size."""
    sniffer, limit, formats = MEDIA_KINDS[kind]
    label = f"Reference {kind} {index}"
    if not data:
        raise ModelHubError(f"{label} is empty.")
    if len(data) > limit:
        raise ModelHubError(f"{label} is larger than {limit // (1024 * 1024)} MB.")
    mime = sniffer(data)
    if not mime:
        raise ModelHubError(f"{label} must be a {formats} file.")
    return (f"{kind}_{index}.{EXTENSIONS[mime]}", data, mime)


def check_media_url(url: str) -> str:
    url = url.strip()
    if url.startswith("data:"):
        raise ModelHubError("data: URIs are not accepted. Upload the file instead.")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ModelHubError(f"Reference links must be public https URLs: {url[:100]}")
    return url


check_image_url = check_media_url


def build_payload(
    model: str,
    prompt: str,
    *,
    duration: int,
    resolution: str,
    ratio: str,
    generate_audio: bool | None = None,
    images: Sequence[MediaRef] = (),
    videos: Sequence[MediaRef] = (),
    audios: Sequence[MediaRef] = (),
    first_last_frames: bool = False,
) -> dict[str, Any]:
    """Build the body for POST /videos/generations.

    Each media item is a public https URL or a ``(filename, bytes, mime)`` tuple.
    With ``first_last_frames`` the first image is the first frame and the optional
    second image the last frame. Some channels need exactly two images for
    ``first_last_frames``, so a first frame without a last frame is sent as a
    single ``omni_reference`` image instead. Otherwise images, videos and audios are
    omni-reference material (``image_file_N`` / ``video_file_N`` / ``audio_file_N``).
    """
    if model not in MODELS:
        raise ModelHubError(f"Unknown model {model!r}. Choose one of: {', '.join(MODELS)}.")
    prompt = (prompt or "").strip()
    if not prompt:
        raise ModelHubError("The prompt is empty.")
    low, high = MODELS[model]
    try:
        seconds = int(duration)
    except (TypeError, ValueError):
        raise ModelHubError("Duration must be a whole number of seconds.") from None
    if not low <= seconds <= high:
        raise ModelHubError(f"{model} makes videos of {low} to {high} seconds; got {seconds}.")
    allowed_resolutions = MODEL_RESOLUTIONS.get(model, RESOLUTIONS)
    if resolution not in allowed_resolutions:
        raise ModelHubError(f"{model} supports these resolutions: {', '.join(allowed_resolutions)}.")
    if ratio not in RATIOS:
        raise ModelHubError(f"Aspect ratio must be one of: {', '.join(RATIOS)}.")
    if first_last_frames:
        if not 1 <= len(images) <= 2 or videos or audios:
            raise ModelHubError(
                "First/last frame mode takes a first frame, an optional last frame and no other references."
            )
    else:
        max_images, max_videos, max_audios = REFERENCE_LIMITS[model]
        for items, limit, kind in ((images, max_images, "images"), (videos, max_videos, "videos"),
                                   (audios, max_audios, "audio files")):
            if len(items) > limit:
                raise ModelHubError(f"{model} accepts at most {limit} reference {kind}; got {len(items)}.")

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "duration": seconds,
        "resolution": resolution,
        "ratio": ratio,
    }
    if generate_audio is not None and model not in NO_AUDIO_OPTION:
        payload["generate_audio"] = bool(generate_audio)
    if images or videos or audios:
        both_frames = first_last_frames and len(images) == 2
        payload["functionMode"] = "first_last_frames" if both_frames else "omni_reference"
        for field, items in (("image_file", images), ("video_file", videos), ("audio_file", audios)):
            for index, item in enumerate(items, 1):
                payload[f"{field}_{index}"] = item if isinstance(item, tuple) else check_media_url(item)
    return payload


def _is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast


def public_host_only(host: str) -> None:
    """Refuse hosts that resolve to private addresses (Dify may run inside a private network)."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ModelHubError(f"Could not resolve the video host {host}.") from exc
    if not infos or not all(_is_public_address(info[4][0]) for info in infos):
        raise ModelHubError(f"Refusing to download the video from a non-public address ({host}).")


def _error_message(reply: requests.Response) -> str:
    try:
        body = reply.json()
    except ValueError:
        return reply.text[:300] or reply.reason
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:300]
        if isinstance(error, str):
            return error[:300]
        for key in ("message", "msg"):
            if body.get(key):
                return str(body[key])[:300]
    return str(body)[:300]


class ModelHubClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = API_BASE,
        session: requests.Session | None = None,
        check_host: Callable[[str], None] = public_host_only,
        poll_seconds: float = POLL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.api_key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.http = session or requests.Session()
        self.check_host = check_host
        self.poll_seconds = poll_seconds
        self.sleep = sleep
        self.clock = clock

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise AuthError("Add your ModelHub API key in the plugin's authorization settings.")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }

    @staticmethod
    def _json(reply: requests.Response) -> dict[str, Any]:
        try:
            body = reply.json()
        except ValueError:
            raise ModelHubError(f"ModelHub sent an unreadable reply (HTTP {reply.status_code}).") from None
        if not isinstance(body, dict):
            raise ModelHubError(f"ModelHub sent an unexpected reply (HTTP {reply.status_code}).")
        data = body.get("data")  # tolerate an older {code, data} wrapper
        if isinstance(data, dict) and ("task_id" in data or "status" in data):
            return data
        return body

    # ------------------------------------------------------------------ key check
    def validate_key(self) -> None:
        """Check the key without starting a job: an unknown task answers 401 or 404."""
        try:
            reply = self.http.get(
                f"{self.base_url}/videos/tasks/dify-credential-check",
                headers=self._headers(),
                timeout=SHORT_TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise ModelHubError(f"Could not reach ModelHub ({type(exc).__name__}). Try again.") from None
        if reply.status_code in (401, 403):
            raise AuthError("ModelHub rejected this API key. Check that it was copied in full and is active.")
        if reply.status_code in (200, 404):
            return
        raise ModelHubError(f"Unexpected reply from ModelHub while checking the key (HTTP {reply.status_code}).")

    # ---------------------------------------------------------------------- submit
    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Start a job. Returns the flat reply (with ``task_id``). Never retries."""
        headers = self._headers()
        files = [(k, v) for k, v in payload.items() if isinstance(v, tuple)]
        if files:
            fields = {
                k: ("true" if v else "false") if isinstance(v, bool) else str(v)
                for k, v in payload.items()
                if not isinstance(v, tuple)
            }
            kwargs: dict[str, Any] = {"data": fields, "files": files}
        else:
            kwargs = {"json": payload}
        try:
            reply = self.http.post(
                f"{self.base_url}/videos/generations",
                headers=headers,
                timeout=SUBMIT_TIMEOUT,
                allow_redirects=False,
                **kwargs,
            )
        except requests.exceptions.ConnectTimeout:
            raise ModelHubError("Could not connect to ModelHub. Nothing was submitted.") from None
        except requests.RequestException as exc:
            raise UnknownOutcome(
                f"The submit request ended without an answer ({type(exc).__name__}). The job may have been "
                "created: check your task history on modelhub.cc before submitting again."
            ) from None
        if reply.status_code >= 500:
            raise UnknownOutcome(
                f"ModelHub answered HTTP {reply.status_code}. The job may have been created: check your task "
                "history on modelhub.cc before submitting again."
            )
        if reply.status_code in (401, 403):
            raise AuthError(f"ModelHub rejected the API key (HTTP {reply.status_code}).")
        if reply.status_code >= 400:
            raise RequestError(
                f"ModelHub rejected the request (HTTP {reply.status_code}): {_error_message(reply)}. "
                "Nothing was charged."
            )
        body = self._json(reply)
        if not body.get("task_id"):
            raise UnknownOutcome("ModelHub accepted the request but sent no task ID. Check your task history.")
        body["task_id"] = str(body["task_id"])
        return body

    # ------------------------------------------------------------------------ poll
    def get_task(self, task_id: str) -> dict[str, Any]:
        task_id = check_task_id(task_id)
        reply = self.http.get(
            f"{self.base_url}/videos/tasks/{task_id}",
            headers=self._headers(),
            timeout=SHORT_TIMEOUT,
            allow_redirects=False,
        )
        if reply.status_code in (401, 403):
            raise AuthError(f"ModelHub rejected the API key (HTTP {reply.status_code}).")
        if reply.status_code == 404:
            raise RequestError(f"Task {task_id} was not found or has expired.")
        if reply.status_code >= 500:
            reply.raise_for_status()
        if reply.status_code >= 400:
            raise RequestError(f"ModelHub rejected the task query (HTTP {reply.status_code}): {_error_message(reply)}")
        body = self._json(reply)
        if "status" not in body:
            raise ModelHubError("ModelHub sent a task reply without a status.")
        body["status"] = str(body["status"]).lower()
        return body

    @staticmethod
    def failure_reason(state: dict[str, Any]) -> str:
        result = state.get("result") if isinstance(state.get("result"), dict) else {}
        for value in (state.get("error"), result.get("error"), state.get("message"), state.get("fail_reason")):
            if isinstance(value, dict):
                value = value.get("message")
            if value:
                return str(value)[:300]
        return "the upstream service did not make the video"

    @staticmethod
    def cancel_reason(state: dict[str, Any]) -> str:
        error = state.get("error")
        if isinstance(error, dict):
            error = error.get("message")
        return str(error)[:300] if error else "the task was cancelled"

    @staticmethod
    def credits(state: dict[str, Any]) -> Any:
        for key in ("creditsConsumed", "credits_consumed"):
            if state.get(key) is not None:
                return state[key]
        return None

    def wait(self, task_id: str, max_seconds: float = WAIT_SECONDS) -> dict[str, Any]:
        """Poll until the task completes. Raises TaskFailed (failed or cancelled) or TaskStillRunning."""
        start = self.clock()
        errors = 0
        state: dict[str, Any] = {"task_id": task_id, "status": "processing"}
        while True:
            try:
                state = self.get_task(task_id)
                errors = 0
            except requests.RequestException:
                errors += 1  # polling is read-only, so transient errors are retried
                if errors > MAX_POLL_ERRORS:
                    raise ModelHubError(
                        f"Lost contact with ModelHub while waiting for task {task_id}. "
                        "Check it later with Get Video Task."
                    ) from None
            else:
                if state["status"] == "completed":
                    if not self.video_url(state):
                        raise ModelHubError(f"Task {task_id} completed without a video link.")
                    return state
                if state["status"] == "failed":
                    raise TaskFailed(
                        f"Task {task_id} failed: {self.failure_reason(state)}. Failed tasks are not charged.",
                        task_id,
                    )
                if state["status"] == "cancelled":
                    raise TaskFailed(f"Task {task_id} was cancelled: {self.cancel_reason(state)}.", task_id)
            if self.clock() - start + self.poll_seconds > max_seconds:
                raise TaskStillRunning(task_id, state)
            self.sleep(self.poll_seconds)

    @staticmethod
    def video_url(state: dict[str, Any]) -> str | None:
        result = state.get("result")
        if isinstance(result, dict) and result.get("url"):
            return str(result["url"])
        return None

    # -------------------------------------------------------------------- download
    def _check_download_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ModelHubError("The video link is not an https URL.")
        self.check_host(parts.hostname)

    def download(self, url: str, limit: int = MAX_VIDEO_BYTES) -> tuple[bytes, str]:
        """Download the finished video (no API key is sent). Returns (data, mime type)."""
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        for _ in range(MAX_REDIRECTS + 1):
            self._check_download_url(url)
            try:
                reply = self.http.get(url, headers=headers, stream=True, timeout=DOWNLOAD_TIMEOUT,
                                      allow_redirects=False)
            except requests.RequestException as exc:
                raise ModelHubError(f"Could not download the video ({type(exc).__name__}).") from None
            if reply.is_redirect and reply.headers.get("Location"):
                url = urljoin(url, reply.headers["Location"])
                reply.close()
                continue
            with reply:
                if reply.status_code != 200:
                    raise ModelHubError(f"Downloading the video failed (HTTP {reply.status_code}).")
                declared = reply.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > limit:
                    raise VideoTooLarge("The video is larger than the file size limit.")
                chunks: list[bytes] = []
                size = 0
                for chunk in reply.iter_content(chunk_size=1 << 20):
                    size += len(chunk)
                    if size > limit:
                        raise VideoTooLarge("The video is larger than the file size limit.")
                    chunks.append(chunk)
                data = b"".join(chunks)
                if not data:
                    raise ModelHubError("The downloaded video is empty.")
                mime = (reply.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if not mime.startswith("video/"):
                    mime = "video/mp4"
                return data, mime
        raise ModelHubError("Too many redirects while downloading the video.")
