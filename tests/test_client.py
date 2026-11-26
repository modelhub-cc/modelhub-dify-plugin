"""Offline tests: every HTTP call goes to a fake session. Run with `python -m pytest tests`."""

from __future__ import annotations

import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mh_client import (  # noqa: E402
    AuthError,
    ModelHubClient,
    ModelHubError,
    RequestError,
    TaskFailed,
    TaskStillRunning,
    UnknownOutcome,
    VideoTooLarge,
    build_payload,
    check_task_id,
)
from tools.generate_video import collect_media  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
JPEG = b"\xff\xd8\xff" + b"0" * 20


class FakeReply:
    def __init__(self, status=200, body=None, content=b"", headers=None):
        self.status_code = status
        self._body = body
        self._content = content
        self.headers = headers or {}
        self.text = "" if body is None else str(body)
        self.reason = "reason"
        self.is_redirect = status in (301, 302, 303, 307, 308)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._content), 4):
            yield self._content[i : i + 4]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def get(self, url, **kwargs):
        return self._next("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._next("POST", url, **kwargs)


def client(replies, **kwargs):
    return ModelHubClient(
        "pk_test", session=FakeSession(replies), check_host=lambda host: None, sleep=lambda s: None, **kwargs
    )


# ---------------------------------------------------------------- payload
def test_payload_text_only():
    p = build_payload("seedance-2.0-mini", " a cat ", duration=4, resolution="480p", ratio="16:9", generate_audio=True)
    assert p == {
        "model": "seedance-2.0-mini",
        "prompt": "a cat",
        "duration": 4,
        "resolution": "480p",
        "ratio": "16:9",
        "generate_audio": True,
    }


def test_payload_references_and_frames():
    p = build_payload("seedance-2.0", "x", duration=5, resolution="720p", ratio="9:16",
                      images=[("a.png", PNG, "image/png"), "https://e.com/b.jpg"])
    assert p["functionMode"] == "omni_reference"
    assert p["image_file_1"][0] == "a.png" and p["image_file_2"] == "https://e.com/b.jpg"
    p = build_payload("seedance-2.0", "x", duration=5, resolution="720p", ratio="9:16",
                      images=[("a.png", PNG, "image/png"), ("b.png", PNG, "image/png")], first_last_frames=True)
    assert p["functionMode"] == "first_last_frames"
    assert p["image_file_1"][0] == "a.png" and p["image_file_2"][0] == "b.png"


def test_first_frame_only_goes_omni_reference():
    # Some channels need exactly two images for first_last_frames (one gives HTTP 400).
    p = build_payload("seedance-2.0", "x", duration=5, resolution="720p", ratio="9:16",
                      images=[("a.png", PNG, "image/png")], first_last_frames=True)
    assert p["functionMode"] == "omni_reference"
    assert p["image_file_1"][0] == "a.png" and "image_file_2" not in p


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": "unknown-model"},
        {"duration": 16},
        {"duration": 3},
        {"resolution": "4k"},
        {"ratio": "2:1"},
        {"prompt": "  "},
        {"images": ["data:image/png;base64,AAAA"]},
        {"images": ["http://e.com/a.jpg"]},
        {"images": ["https://e.com/a.jpg"] * 10},
        {"videos": ["https://e.com/a.mp4"] * 4},
    ],
)
def test_payload_rejects(kwargs):
    base = {"model": "seedance-2.0-mini", "prompt": "x", "duration": 5, "resolution": "720p", "ratio": "16:9"}
    base.update(kwargs)
    model, prompt = base.pop("model"), base.pop("prompt")
    with pytest.raises(ModelHubError):
        build_payload(model, prompt, **base)


def test_seedance_25_allows_30_seconds():
    assert build_payload("seedance-2.5", "x", duration=30, resolution="1080p", ratio="16:9")["duration"] == 30


def test_task_id_check():
    assert check_task_id(" abc-123 ") == "abc-123"
    for bad in ("", "../x", "a/b", "a?b=1"):
        with pytest.raises(ModelHubError):
            check_task_id(bad)


# ---------------------------------------------------------------- images from Dify
class Upload:
    def __init__(self, blob):
        self.blob = blob


MP4 = b"\x00\x00\x00\x18ftypisom" + b"0" * 20
MP3 = b"ID3" + b"0" * 20


def test_collect_media_rules():
    media, frames = collect_media({
        "reference_images": [Upload(PNG)], "reference_image_urls": "https://e.com/a.jpg",
        "reference_videos": [Upload(MP4)], "reference_audio_urls": "https://e.com/a.mp3, https://e.com/b.mp3",
        "reference_audios": [Upload(MP3)],
    })
    assert not frames
    assert media["images"][0][2] == "image/png" and media["images"][1] == "https://e.com/a.jpg"
    assert media["videos"][0][2] == "video/mp4"
    assert media["audios"][0][2] == "audio/mpeg" and media["audios"][1:] == ["https://e.com/a.mp3", "https://e.com/b.mp3"]
    media, frames = collect_media({"first_frame": Upload(JPEG), "last_frame": Upload(PNG)})
    assert frames and [i[2] for i in media["images"]] == ["image/jpeg", "image/png"]
    with pytest.raises(ModelHubError):
        collect_media({"last_frame": Upload(PNG)})
    with pytest.raises(ModelHubError):
        collect_media({"first_frame": Upload(PNG), "reference_videos": [Upload(MP4)]})
    with pytest.raises(ModelHubError):
        collect_media({"reference_images": [Upload(b"GIF89a....")]})
    with pytest.raises(ModelHubError):
        collect_media({"reference_videos": [Upload(PNG)]})


@pytest.mark.parametrize("model,images,videos,audios,ok", [
    ("seedance-2.0-mini", 9, 3, 3, True),
    ("seedance-2.0-mini", 10, 0, 0, False),
    ("seedance-2.0-fast", 0, 4, 0, False),
    ("seedance-2.0", 0, 0, 4, False),
    ("seedance-2.5", 30, 10, 10, True),
    ("seedance-2.5", 31, 0, 0, False),
    ("seedance-2.5-flash", 10, 5, 5, True),
    ("seedance-2.5-flash", 0, 6, 0, False),
])
def test_reference_limits_per_model(model, images, videos, audios, ok):
    res = "720p"
    kwargs = dict(duration=5, resolution=res, ratio="16:9",
                  images=["https://e.com/i.jpg"] * images, videos=["https://e.com/v.mp4"] * videos,
                  audios=["https://e.com/a.mp3"] * audios)
    if ok:
        p = build_payload(model, "x", **kwargs)
        assert (f"image_file_{images}" in p) == bool(images)
        assert (f"video_file_{videos}" in p) == bool(videos)
        assert (f"audio_file_{audios}" in p) == bool(audios)
        assert p["functionMode"] == "omni_reference"
    else:
        with pytest.raises(ModelHubError):
            build_payload(model, "x", **kwargs)


def test_flash_rules():
    with pytest.raises(ModelHubError):
        build_payload("seedance-2.5-flash", "x", duration=5, resolution="1080p", ratio="16:9")
    p = build_payload("seedance-2.5-flash", "x", duration=30, resolution="720p", ratio="16:9", generate_audio=True)
    assert "generate_audio" not in p


def test_first_last_frames_reject_other_media():
    with pytest.raises(ModelHubError):
        build_payload("seedance-2.0", "x", duration=5, resolution="720p", ratio="16:9",
                      images=["https://e.com/a.jpg"], videos=["https://e.com/v.mp4"], first_last_frames=True)


# ---------------------------------------------------------------- key check
@pytest.mark.parametrize("status,error", [(404, None), (401, AuthError), (403, AuthError), (500, ModelHubError)])
def test_validate_key(status, error):
    c = client([FakeReply(status, {"error": {"message": "m"}})])
    if error:
        with pytest.raises(error):
            c.validate_key()
    else:
        c.validate_key()
    method, url, kwargs = c.http.calls[0]
    assert url.endswith("/videos/tasks/dify-credential-check")
    assert kwargs["headers"]["Authorization"] == "Bearer pk_test"


def test_empty_key_never_calls_api():
    c = ModelHubClient("", session=FakeSession([]))
    with pytest.raises(AuthError):
        c.validate_key()


# ---------------------------------------------------------------- submit
def test_submit_json_and_multipart():
    c = client([FakeReply(200, {"task_id": "t1", "status": "processing"})] * 2)
    c.submit(build_payload("seedance-2.0-mini", "x", duration=4, resolution="480p", ratio="16:9"))
    assert "json" in c.http.calls[0][2]
    c.submit(build_payload("seedance-2.0-mini", "x", duration=4, resolution="480p", ratio="16:9",
                           generate_audio=False, images=[("a.png", PNG, "image/png")]))
    kwargs = c.http.calls[1][2]
    assert kwargs["files"][0][0] == "image_file_1"
    assert kwargs["data"]["duration"] == "4" and kwargs["data"]["generate_audio"] == "false"


def test_submit_errors():
    with pytest.raises(RequestError, match="Nothing was charged"):
        client([FakeReply(400, {"error": {"message": "bad ratio"}})]).submit({"model": "m"})
    with pytest.raises(AuthError):
        client([FakeReply(401, {"error": {"message": "no"}})]).submit({"model": "m"})
    with pytest.raises(UnknownOutcome):
        client([FakeReply(502, None)]).submit({"model": "m"})
    with pytest.raises(UnknownOutcome):
        client([requests.ReadTimeout()]).submit({"model": "m"})
    with pytest.raises(ModelHubError):
        client([requests.ConnectTimeout()]).submit({"model": "m"})


def test_error_messages_never_contain_key():
    c = client([FakeReply(400, {"error": {"message": "bad"}})])
    with pytest.raises(ModelHubError) as info:
        c.submit({"model": "m"})
    assert "pk_test" not in str(info.value)


# ---------------------------------------------------------------- wait
def test_wait_completes_after_transient_error():
    done = {"status": "completed", "result": {"url": "https://media.example/v.mp4"}, "creditsConsumed": 60}
    c = client([FakeReply(200, {"status": "processing", "progress": 0}), requests.ConnectionError(), FakeReply(200, done)])
    assert c.wait("t1")["creditsConsumed"] == 60


def test_wait_failed_and_timeout():
    with pytest.raises(TaskFailed, match="not charged"):
        client([FakeReply(200, {"status": "failed", "result": {"error": "moderation"}})]).wait("t1")
    ticks = iter(range(0, 10000, 100))
    c = client([FakeReply(200, {"status": "processing"})] * 10, clock=lambda: next(ticks))
    with pytest.raises(TaskStillRunning):
        c.wait("t1", max_seconds=250)


def test_get_task_not_found():
    with pytest.raises(RequestError, match="not found"):
        client([FakeReply(404, {"error": {"message": "x"}})]).get_task("t1")


# ---------------------------------------------------------------- download
def test_download_ok_without_key_and_with_redirect():
    c = client([
        FakeReply(302, None, headers={"Location": "https://cdn.example/v.mp4"}),
        FakeReply(200, None, content=b"0123456789", headers={"Content-Type": "video/mp4"}),
    ])
    data, mime = c.download("https://media.example/v.mp4")
    assert data == b"0123456789" and mime == "video/mp4"
    for _, url, kwargs in c.http.calls:
        assert "Authorization" not in kwargs["headers"]
        assert kwargs["headers"]["User-Agent"].startswith("Mozilla/5.0")


def test_download_limits():
    with pytest.raises(VideoTooLarge):
        client([FakeReply(200, None, content=b"0" * 20)]).download("https://m.example/v.mp4", limit=10)
    with pytest.raises(ModelHubError):
        client([]).download("http://m.example/v.mp4")


def test_private_hosts_refused():
    c = ModelHubClient("pk_test", session=FakeSession([]))
    with pytest.raises(ModelHubError):
        c.download("https://127.0.0.1/v.mp4")


# ---------------------------------------------------------------- contract: cancelled / error / credits
def test_wait_cancelled_is_final():
    c = client([FakeReply(200, {"status": "cancelled", "error": "任务已取消"}),
                FakeReply(200, {"status": "processing"})])
    with pytest.raises(TaskFailed, match="cancelled: 任务已取消"):
        c.wait("t1")
    assert len(c.http.calls) == 1  # no further polling


def test_wait_cancelled_default_reason():
    with pytest.raises(TaskFailed, match="was cancelled"):
        client([FakeReply(200, {"status": "CANCELLED"})]).wait("t1")


def test_failure_reason_reads_top_level_error_first():
    state = {"status": "failed", "error": "content blocked", "error_code": "x", "failureType": "blocked",
             "creditsConsumed": None, "result": {"error": "old"}}
    assert ModelHubClient.failure_reason(state) == "content blocked"
    assert ModelHubClient.failure_reason({"result": {"error": "legacy"}}) == "legacy"
    assert ModelHubClient.failure_reason({"error": {"message": "nested"}}) == "nested"
    with pytest.raises(TaskFailed, match="content blocked"):
        client([FakeReply(200, state)]).wait("t1")


def test_credits_both_spellings():
    from tools._common import summary
    assert ModelHubClient.credits({"creditsConsumed": 60}) == 60
    assert ModelHubClient.credits({"credits_consumed": 45}) == 45
    assert ModelHubClient.credits({"creditsConsumed": None}) is None
    assert summary({"status": "completed", "credits_consumed": 45})["credits_consumed"] == 45
    assert summary({"status": "completed", "creditsConsumed": 60})["credits_consumed"] == 60


class FakeRuntime:
    def __init__(self):
        self.credentials = {"api_key": "pk_test"}


def _get_task_tool(state):
    from tools import get_video_task as mod
    tool = object.__new__(mod.GetVideoTaskTool)
    tool.runtime = FakeRuntime()
    tool.create_json_message = lambda d: ("json", d)
    tool.create_text_message = lambda t: ("text", t)
    fake = client([FakeReply(200, state)])
    return mod, tool, fake


@pytest.mark.parametrize("state,match", [
    ({"status": "cancelled", "error": "任务已取消"}, "cancelled: 任务已取消"),
    ({"status": "failed", "error": "bad prompt"}, "failed: bad prompt"),
])
def test_get_video_task_final_states(monkeypatch, state, match):
    mod, tool, fake = _get_task_tool(state)
    monkeypatch.setattr(mod, "client_for", lambda _tool: fake)
    with pytest.raises(ValueError, match=match):
        list(tool._invoke({"task_id": "t1"}))


def test_submit_multipart_never_sends_data_uri():
    c = client([FakeReply(200, {"task_id": "t1", "status": "submitted"})])
    media, frames = collect_media({"reference_images": [Upload(PNG)], "reference_videos": [Upload(MP4)],
                                   "reference_audios": [Upload(MP3)]})
    c.submit(build_payload("seedance-2.0", "x", duration=5, resolution="720p", ratio="16:9",
                           first_last_frames=frames, **media))
    kwargs = c.http.calls[0][2]
    assert [f[0] for f in kwargs["files"]] == ["image_file_1", "video_file_1", "audio_file_1"]
    assert not any("data:" in str(v) for v in kwargs["data"].values())
    assert kwargs["data"]["functionMode"] == "omni_reference"
