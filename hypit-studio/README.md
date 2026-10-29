# Hypit Studio — reference-guided ad remakes

An HTML/CSS/JavaScript interface with a local Python backend for recreating UGC ads, using Runware and WaveSpeed. The original repository's recovery-kit files remain unchanged. The existing standalone canvas editor is available as **Motion lab**.

## Run

On Windows, use the downloadable Windows ZIP: extract it, install Python 3.13 x64, run `Setup.bat` once, then `Start.bat`. See `START-HERE-WINDOWS.txt`. It includes Windows wheels; FFmpeg is downloaded and checksum-verified during first setup. The shell commands below are for Linux/macOS.

Requires Python 3.10+, FFmpeg and ffprobe. Dependencies install into this folder's ignored virtual environment.

```sh
cd hypit-studio
bash setup.sh
bash start.sh
```

Open the app on port **8081** in your browser. Run a single backend process; the file-based job store is designed for one local user. Do not expose it as an unauthenticated public service. `HYPIT_PORT` changes the port. `HYPIT_DATA_DIR` changes the local media/project directory (default `.data/`). The service serves only frontend assets and selected project media, not configuration or backend files.

Open **Connections** and enter provider keys, or configure these server environment variables before starting:

- `RUNWARE_API_KEY`
- `WAVESPEED_API_KEY`
- `WAVESPEED_VISION_MODEL` — optional vision-capable model ID enabled in your WaveSpeed LLM account

Keys entered in Connections stay in server memory until the process stops. They are never saved in projects, JavaScript, browser storage or returned by the API. Use environment settings for persistent secrets. Configure a vision model ID using the account's available models; the app does not assume a text-only model accepts images. **Verify** checks read-only account/catalog access, not all model entitlements or output quality.

The main app requires the backend. Opening `index.html` directly shows startup instructions; Motion lab still works without a server.

## The UGC remake workflow

1. **Reference:** Upload an ad (0.5–180 seconds, up to 200MB). FFmpeg detects cuts and extracts clips, opening/middle/closing frames and original audio. Uninterrupted takes are split into segments up to 8 seconds. Review/edit cut boundaries before generation; custom shots may be 0.25–10 seconds.
2. **Recast:** Add your product name, approved facts and a clear packshot. Describe the new presenter and room, and optionally provide their reference images. Reuse these anchors across shots. Choose frame, motion and speech providers independently. Add separate background music and optional exact end-card copy.
3. **Shot studio:** For each shot, describe the source action/camera and write the replacement line. Optional WaveSpeed visual analysis uses the three frames to describe shots and adapts **supplied** dialogue to your product facts. It cannot hear the ad. Optional transcription searches the live catalog for an audio-input Whisper model and requires timestamped JSON segments; if the provider doesn't expose that contract, enter dialogue manually. AI analysis must be reviewed before rendering.
4. **Approve a replacement frame:** Generate an image using the source composition, product and cast/room references. Inspect hand contact, appearance, room and packaging. Approve the frame before generating motion. You may upload a hand-corrected image instead.
5. **Generate motion:** Use **Presenter performance** for a visible single presenter. This drives the approved image with the original shot's gestures and expressions. Use **Video edit** for product inserts, moving cameras, or shots where image-driven performance isn't suitable. Input segments shorter than 3 seconds are padded for performance models; the final take is trimmed back to the reference edit. Uploaded corrected clips are supported.
6. **Speech:** Choose rewritten dialogue + TTS when changing product names or claims. Choose original-word revoicing to synthesize the supplied source transcript in a new voice. This retains the words, not the source recording’s prosody. Upload a voice-converted recording if exact delivery is needed. Uploading a voice take is also supported. Speech is padded to the shot; if it is slightly too long, tempo adjustment is capped at 1.25×. Longer lines are rejected instead of truncated.
7. **Lip sync:** For a visible speaker with new speech, run the Runware or WaveSpeed video+audio lip-sync stage or upload a corrected lip-synced clip. Off-screen narration can skip this stage. The side-by-side replacement preview includes the selected speech; **Play both** mutes the source so the new line can be reviewed against the reference motion.
8. **Review and export:** Review product fidelity, identity, setting, action/timing, speech and mouth movement for every shot. Export is blocked until every shot has been approved. The app never silently uses the original ad to fill a missing generation. It assembles H.264/AAC MP4 in reference order, mixes selected speech and optional music, and optionally overlays the real product photo + exact CTA in the final two seconds. A JSON review report accompanies the video.

No paid operation starts automatically. Buttons explicitly request a provider operation; provider accounts bill generation/analysis. Make one good shot before rendering the rest. Changing the product/cast/room invalidates affected visual approvals. Changing dialogue retains visuals and invalidates speech/lip sync. CTA-only changes require only a new local assembly. Uploaded media, takes, job IDs and projects survive restarts in `.data/`.

## Provider adapters

| Stage | Runware | WaveSpeed |
|---|---|---|
| Replacement reference frame | Nano Banana 2 (`google:4@3`) with `inputs.referenceImages` | `google/nano-banana-2/edit` |
| Presenter performance | Pruna P-Video-Animate (`prunaai:p-video@animate`), one image + one motion video | `wavespeed-ai/wan-2.2/animate` |
| Reference video edit | Luma Ray 3.2 (`luma:ray@3.2`), source video + approved opening frame | `bytedance/seedance-2.0/video-edit` |
| Rewrite / original-word revoicing | Inworld TTS-2 (`inworld:tts@2`), named voice such as Sarah | `minimax/speech-2.6-turbo` |
| Lip sync | sync-3 (`sync:3@0`), `inputs.video` + `inputs.audio` | Discover a video+audio lip-sync model from the live catalog |
| Optional visual analysis | Manual planning | OpenAI-compatible WaveSpeed LLM endpoint with your vision model ID |

Runware uses **`https://api.runware.ai/v1`**, bearer authentication, and a JSON array of tasks. It is a different provider/API from Runway. Generation requests use AIR model identifiers, a client-selected `taskUUID`, `deliveryMethod: async`, and `getResponse` polling. Files are base64 uploaded with `mediaStorage` / `operation: upload`; returned `mediaUUID` values are used in model inputs. The adapter limits each inline upload to 30MB. Reference images are retained separately, including cast and room assets; there is no obsolete three-reference restriction.

Before generating, the app retrieves the model's **`requestSchema` from `https://schemas.runware.ai/resolve/{AIR}`** and validates the actual task. Missing schemas and invalid inputs stop submission; the app does not silently drop reference assets or substitute a text-to-video request. Runware Nano Banana 2 uses the documented `resolution: 1K` mode and is prompted to preserve the reference aspect/composition. Final assembly pads to the source ratio without cropping. Confirm actual composition during frame review. Luma editing uses automatic source conditioning and an approved opening-frame anchor, without incompatible width/height/duration overrides.

WaveSpeed still uses `https://api.wavespeed.ai/api/v3`, upload-ticket + signed PUT, and asynchronous prediction polling. The live catalog supplies its request schemas. Frame, motion, voice and lip-sync providers can be mixed independently. Model IDs, limits, availability and pricing can change; live account validation is required.

Adapters were checked against official sources on 2026-10-29:

- [Runware Python SDK](https://github.com/Runware/sdk-python/tree/5cad93528cd6b7b79c76209e2ffd978b8d22c84f)
- [Runware CLI HTTP/schema contracts](https://github.com/Runware/runware-cli/tree/f73899b9aa0700ab992c2e8451c2a941542a49ed)
- [Runware documented model requests](https://github.com/Runware/runware-skills/tree/469b6979794bb70f020d9a92fb1bf6f64fa1aee4)
- [WaveSpeed Python SDK](https://github.com/WaveSpeedAI/wavespeed-python/tree/7bcfc516a9110256323601831fd5f5bab8b923c1)
- [WaveSpeed MCP model catalog and planning](https://github.com/WaveSpeedAI/mcp-server/tree/588d7718be58d22e5221d31dc59f2270f1b7169d)

Existing project settings from the earlier provider are migrated to Runware on first read or startup, preserving media, approvals and historical task IDs. Existing WaveSpeed lip-sync routing is retained; new projects default to Runware. Old provider tasks **cannot** be resumed against Runware. Existing API secrets are not reused across providers. If an earlier environment draft contains the mistaken `RUNWAYML_API_SECRET` requirement, remove it in environment settings and provide `RUNWARE_API_KEY` instead; the configuration tool can add requirements but cannot delete them.

## Interrupted renders and network access

The app records provider task IDs before polling and persists every state transition. A failed download or server restart can resume the existing remote task. Paid generation POSTs are **never automatically retried**. Runware’s task UUID is persisted **before** the generation POST, so an uncertain submission can be polled with the same UUID. If a WaveSpeed submission times out before returning a task ID, it is marked **unknown**. Check the provider dashboard, recover its task ID, or explicitly dismiss the record before submitting the same stage again. A synchronous vision request has no asynchronous task to recover; review that provider request before dismissing it. Local assembly failures can be rerun without provider charges.

Allow HTTPS access to `api.runware.ai`, `schemas.runware.ai`, `api.wavespeed.ai`, and `llm.wavespeed.ai`, plus Runware output hosts (`im.runware.ai`, `vm.runware.ai`, `am.runware.ai`) and storage hosts returned by WaveSpeed. Credentials go only to API hosts, never to signed storage hosts. Error messages identify a blocked storage hostname without revealing signed URL parameters. HTTP proxy settings and TLS verification remain enabled. Localhost and private-network media URLs are rejected.

## What “same video” means here

This is a reference-guided, human-reviewed production tool, not a guarantee of pixel-identical object replacement. It preserves the source edit's shot order and verifies final runtime within 0.15 seconds. Output uses 30fps; cuts are quantized to that frame grid. Provider/model aspect-ratio limits may require padding. The app cannot guarantee the same exact gestures, camera trajectory, hands, occlusions, packaging typography, identity or lighting from a generative model. Multi-person interactions and heavily occluded products often require manual compositing or uploaded corrected takes. The quality checklist records a person's review; it is not an automated similarity score.

Source music, dialogue and sound effects are not automatically separated into stems. Choosing replacement speech removes the original audio; upload music/ambience separately. Product lettering inside generated footage may drift; the optional local packshot/CTA overlay is exact. This app supports provider preset/configured voices, not arbitrary voice cloning from a reference ad. It does not include the Hypit runtime or call its rendering engine.

## Validation

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Offline tests exercise provider payloads/authentication, unsigned uploads, live-schema validation, unknown submissions, resume without re-submission, private-URL rejection, cut detection, real FFmpeg processing, voice duration limits, source-audio removal, approval gates, final MP4 assembly and stale-result invalidation. They use synthetic footage and mocked provider responses; no provider credits are spent. Browser validation covers the HTML workflow, uploads, approval controls, synchronized preview, persistent media, MP4 download and mobile layout.

**Live Runware/WaveSpeed generation has not been validated in this environment: provider credentials are not present and runtime provider network access must be configured.** Mocked contracts and working local exports do not establish model quality or live API availability. Connect your accounts and make a short, single-shot test with your actual reference and product before a full render.
