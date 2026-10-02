# Video workspace improvements (Runtime v0.17)

This change refines the existing **Wan2.2 TI2V 5B text-to-video** and **HunyuanVideo 1.5 720p text-to-video** adapters. It does not claim new image-to-video/Animate/14B adapter support or NSFW generation. Models without an implemented adapter remain unavailable for direct launch.

## User-visible changes

- Validate dimensions, frames, FPS, steps, CFG and seed in both browser and Runtime before model/backend preparation.
- Dimensions must be multiples of 16; frames must be `4n+1` within 9–241. The existing Hunyuan adapter stays at 1280×720.
- Display estimated clip duration (`frames / fps`), actual job elapsed time and the generated seed for reproduction. This is not an inference ETA or a promise of deterministic GPU output.
- CFG `0` and seed `0` are preserved, not replaced by defaults. Fractional integers, unsafe seeds, NaN/Infinity, overlong text and unsupported dimensions are rejected.
- Download progress is shown only during downloads, never recycled as a fake 100% generation progress indicator.
- Reopen a workspace to inspect an existing task. Closing it clears the local preview but does not cancel the worker. Network loss causes status retries, not a false success.
- Result preview and **下载生成的视频** use the existing authenticated Runtime fetch. `/v1/video/file` no longer exempts remote-token checks. No token is put into a media URL.
- Browser preview depends on codec/container support; unsupported formats can still be downloaded.

## Lifecycle and result integrity

Stop reports `cancelling` while an actual worker is still exiting. It removes the owned queued prompt, interrupts its backend operation and prevents a cancelled task from becoming complete. Stopping an idle video workspace does not interrupt an unrelated ComfyUI image task. Image/video/edit starts share one arbitration lock.

History output is resolved from the configured output node for the current prompt, constrained to ComfyUI's output directory and supported nonempty video files. A missing output is an error; the previous fallback to any recent video is removed. Incomplete downloads retain `.part` data instead of marking a truncated file ready.

## Upgrade and verification

Update both website and Runtime to v0.17. The backend still uses the existing managed ComfyUI build, cached model downloads and hardware checks; no model weights are shipped in this source package. The selected Drive video package may not be the exact artifact used by this preexisting managed adapter: its workflow still declares official download URLs. Full Drive-only 14B/I2V support is not implemented here.

Tests cover browser payloads, authorized result fetch, server authentication, strict parameter validation, download completion, history confinement, cancellation and fixture lifecycle success/failure. No actual GPU video quality, frame consistency, style preservation or live production deployment was tested locally.
