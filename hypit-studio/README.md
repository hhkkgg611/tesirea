# Hypit Studio — make a reference video yours

A simple HTML app with a local Python backend. **Upload a video → review five keyframes → describe your changes → recreate the frames → create and download your video.** All AI steps use **Runware**. The original repository’s recovery-kit files are unchanged.

## Run on Windows

Download [Hypit-Studio-Windows.zip](https://github.com/hhkkgg611/tesirea/raw/refs/heads/hypit-studio-windows/hypit-studio/downloads/Hypit-Studio-Windows.zip). Install **Python 3.13 x64**, extract the whole ZIP, run **Setup.bat** once, then **Start.bat**. Open `http://127.0.0.1:8081`. See `START-HERE-WINDOWS.txt` for troubleshooting.

The ZIP includes Windows Python dependencies. First setup downloads and checksum-verifies FFmpeg. Keep the Start.bat console open while using the app. Cloud publishing is not needed on your PC.

On Linux/macOS, install Python 3.10+, FFmpeg and ffprobe, then:

```sh
cd hypit-studio
bash setup.sh
bash start.sh
```

## The workflow

1. **Connect Runware once.** Click **Connect Runware** and paste your key. One key handles every AI stage. Keys stay in server memory for the session; they are never saved in projects or browser storage. You can also set `RUNWARE_API_KEY` before starting.
2. **Upload a reference video.** Runware analyzes its visuals and audio, selects exactly five useful timestamps, describes the source clips, and transcribes clearly audible words. The frames shown are real frames extracted from your video. Without a key, the app shows five clearly labeled local preview frames until you connect.
3. **Describe your changes.** For example: “Make the table blue. Keep everything else the same.” Optionally attach a character, product or room photo and mention it in your prompt. A product photo and product brief are not required for other edits. Click a keyframe to add a note for only that moment.
4. **Recreate 5 frames.** Runware edits the five frames using your prompt and photos. Review the original and new versions side by side. Recreate an individual frame if needed.
5. **Create video.** This uses the edited frames and original video motion. The app handles all internal clips, opening frames, voice generation, lip sync and assembly. Review the final comparison, then download the MP4. There are no per-shot forms or mandatory checklists.

**Script & voice** is optional. Keep the original soundtrack, select **New voice** to supply a replacement script, or select **No audio**. With New voice and no supplied script, the original transcribed words are retained. Supplied script words must stay in order without additions or omissions. Choose the visible-speaker option for automatic lip sync, or Voiceover for narration. Replacing speech removes the original soundtrack, including its music; source audio is not automatically separated into stems.

AI requests use your Runware account credits. Uploading with a connected account starts analysis. Recreate frames and Create video explicitly start their respective generations. Longer references need several internal clips and additional opening-frame generations. Local FFmpeg extraction, previews and assembly do not call another paid AI service.

## How it works

| Step | Runware model / local operation |
|---|---|
| Video understanding, five keyframe timestamps and transcript | Gemini 3.1 Pro (`google:gemini@3.1-pro`), `textInference`, `inputs.videos` |
| Edit planning and script allocation | Gemini 3.1 Pro, structured JSON output |
| Five keyframes and internal clip opening frames | Nano Banana 2 (`google:4@3`), labeled reference images |
| Source-video editing | Luma Ray 3.2 (`luma:ray@3.2`), source motion + edited opening frame |
| Single-presenter replacement, when the planner selects it | Pruna P-Video-Animate (`prunaai:p-video@animate`) |
| New speech | Inworld TTS-2 (`inworld:tts@2`) |
| Visible-speaker lip sync | Sync-3 (`sync:3@0`), video + new speech |
| Frame extraction, audio fitting, MP4 assembly | Local FFmpeg / Pillow |

The five visible frames are appearance anchors. The backend preserves the actual source cut order, splits long takes into manageable clips, and automatically makes matching opening frames from the nearest edited anchors. It edits each source clip rather than generating an unrelated video from a text prompt. Original timing and aspect ratio are retained in final assembly; runtime is checked within 0.15 seconds on a 30 fps timeline.

Runware uses `https://api.runware.ai/v1` with bearer authentication and task arrays. The exact request is checked against the live model schema at `https://schemas.runware.ai/resolve/{AIR}` before a paid request. Client-selected task UUIDs are saved **before** submission. Analysis and plans use asynchronous `textInference`; images, video and speech use their corresponding inference tasks. Schema changes or unavailable models stop the step with an error.

A failed step keeps completed results. **Continue saved work** polls existing submitted tasks without creating duplicate paid requests. **Generation details** exposes task IDs and errors. Creating a fresh task after failure requires the explicit **Retry this step with a new task** action; check the prior task in Runware first. Closing the browser does not stop the server’s render. After a server restart, reconnect your key and continue saved work.

The earlier WaveSpeed adapters and legacy project endpoints remain available in the backend for compatibility. The simplified interface and its entire AI pipeline use Runware; no WaveSpeed key or second AI provider is required.

## Files and limits

- Reference video: 0.5–180 seconds, up to 200 MB. Optional photos: up to 30 MB each.
- Projects, uploaded media, results and task IDs: `.data/`. Back up this folder.
- `HYPIT_PORT` changes port 8081. `HYPIT_DATA_DIR` changes the project directory.
- Run one backend process for one local user. Bind to localhost; this is not an authenticated public service.
- Allow HTTPS to `api.runware.ai`, `schemas.runware.ai`, and returned media hosts such as `im.runware.ai`, `vm.runware.ai`, `am.runware.ai`. Proxy and TLS verification remain enabled. API keys are never sent to media hosts.

AI edits can drift in faces, hands, packaging text, motion or lighting. Five reference frames improve direction; they do not guarantee pixel-identical selective replacement. Review the full draft before use. The app does not claim automatic fidelity scores or mark internal clips as manually reviewed. Unclear source words stay empty; edit the script when needed. New speech must fit its clip; overly long lines stop rather than being silently truncated. No generated sample is included or represented as a live API result.

## Validation

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover the complete five-frame flow without mandatory product assets, new scripts and automatic voice/lip sync, incomplete-plan rejection, script word preservation, selective invalidation, resuming an uncertain submission without another paid POST, and real FFmpeg MP4 exports. Existing provider, security, media and Windows packaging checks remain.

**Paid Runware generation has not been tested here:** account credentials are absent. API contracts are based on the official [Runware SDK](https://github.com/Runware/sdk-python/tree/5cad93528cd6b7b79c76209e2ffd978b8d22c84f) and [model request examples](https://github.com/Runware/runware-skills/tree/469b6979794bb70f020d9a92fb1bf6f64fa1aee4), with live schema checks at runtime. Offline integration tests use mocked provider responses and synthetic footage. A native Windows machine was unavailable to execute the batch launchers; Windows dependencies, installer extraction and archive integrity were checked in the Linux build environment.
