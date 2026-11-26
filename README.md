# ModelHub (Seedance Video)

Generate Seedance 2.0 and 2.5 videos in Dify through [ModelHub](https://modelhub.cc).

ModelHub is an independent API service. It is not affiliated with or endorsed by ByteDance; the model name only says which model this plugin calls.

- **Author:** modelhub-cc
- **Version:** 0.0.1
- **Type:** tool
- **Source code:** https://github.com/modelhub-cc/modelhub-dify-plugin
- **Contact:** lakers202601@163.com

## What it does

| Tool | What it does |
|---|---|
| Generate Video | Makes a video from a prompt. Optional reference images, reference videos and reference audio (files or https links), or a first and last frame. Waits up to 8 minutes and returns the video as a file. |
| Get Video Task | Looks up a task by its ID and returns the video once it is ready. A task is `processing`, `completed`, `failed` or `cancelled`; failed and cancelled tasks end with the reason from ModelHub. Checking is free. |

| Model | Length | Resolutions | Max references (images / videos / audio) |
|---|---|---|---|
| `seedance-2.0-mini` (cheapest) | 4–15 s | 480p, 720p, 1080p | 9 / 3 / 3 |
| `seedance-2.0-fast` | 4–15 s | 480p, 720p, 1080p | 9 / 3 / 3 |
| `seedance-2.0` | 4–15 s | 480p, 720p, 1080p | 9 / 3 / 3 |
| `seedance-2.5` | 4–30 s | 480p, 720p, 1080p | 30 / 10 / 10 |
| `seedance-2.5-flash` | 4–30 s | 480p, 720p | 10 / 5 / 5 |

Aspect ratios 16:9, 9:16, 1:1, 4:3, 3:4 and 21:9. Sound is generated with the video unless you turn it off (Seedance 2.5 Flash has no sound option).

## Setup

1. Sign up at [modelhub.cc](https://modelhub.cc) and add credits to your account.
2. In the ModelHub console, create an API key. It starts with `pk_`.
3. In Dify, open **Plugins**, install **ModelHub (Seedance Video)**, then click **Authorize** and paste the key. Dify checks the key when you save it; the check does not start a job and costs nothing.
4. Add **Generate Video** to a workflow, chatflow or agent.

## Usage

**Text to video:** fill in **Prompt**, pick a model, duration and resolution.

**References (omni reference):** combine any of
- **Reference images** (JPEG, PNG, WebP) for people, products and scenes,
- **Reference videos** (MP4, MOV, WebM) for motion, camera work or style,
- **Reference audio** (MP3, WAV, M4A, AAC, OGG, FLAC) for voice, music or rhythm.

Each kind can be uploaded as files or given as public https links (separated by commas or new lines); files and links count together toward the model's limit in the table above. ModelHub may also cap the combined number of images, videos and audio files for a request; if so it rejects the request (HTTP 4xx, not charged) with a message. Files are numbered first, then links. Mention in the prompt what each item is for, for example "the woman in image 1 holds the bottle in image 2, moving like video 1".

**Referring to items in the prompt (optional):** in omni-reference mode you can point at a specific item with `@image_file_1`, `@video_file_1`, `@audio_file_1` and so on. Chinese prompts can use the Chinese form for "image 1" instead (second example below). Numbers must start at 1 and have no gaps, following the order above (files first, then links). This is optional: prompts without `@` references also work.

```
@image_file_1 walks into the room from @image_file_2, moving like @video_file_1
@图1 从 @图2 的门口走进来
```

**First and last frame:** attach a **First frame** and, optionally, a **Last frame**. This mode cannot be combined with other references. With only a first frame, the image is sent as a single omni reference image (some upstream channels need both frames for first/last frame mode).

**Long jobs:** a video usually takes one to five minutes. If it is not ready after 8 minutes, the tool returns the task ID instead of failing; pass it to **Get Video Task** later. In a workflow you can also turn off **Wait for the video** to get the task ID at once and fetch the result in a later step. Give the tool node a timeout of at least 10 minutes.

**Outputs:** the video file (`files`), a JSON object with `task_id`, `status`, `model`, `credits_consumed` and `video_url`, and a short text. `video_url` is a temporary link; save the file if you need to keep it. Videos over 50 MB are not attached; you get the temporary link instead.

## Pricing

- Billed per second of output video, in ModelHub credits. **The length of reference videos is billed too**: their total length, rounded up to whole seconds, is added to the output length. The price depends on the model and resolution; see the live price list at [modelhub.cc/models](https://modelhub.cc/models) (also available as JSON at `https://modelhub.cc/v1/models`).
- Rejected requests (HTTP 4xx) and tasks that fail are not charged.
- Checking a task with Get Video Task is free.

## Requirements

- A ModelHub API key with credits.
- Outbound HTTPS access to `modelhub.cc` (API) and `media.modelhub.cc` (finished videos). Videos are downloaded without your key. The plugin refuses to download from private network addresses.

## Privacy

See [PRIVACY.md](PRIVACY.md). In short: your prompt, images and settings are sent to ModelHub to make the video; the plugin itself stores nothing.

## Support

- Issues: https://github.com/modelhub-cc/modelhub-dify-plugin/issues
- Email: lakers202601@163.com
