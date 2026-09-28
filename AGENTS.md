# Model project handoff

Read README.md, governance/RELEASE_STATE.json, governance/PROJECT_STATE.md,
governance/ARCHITECTURE.md, docs/ACTIVATION_AND_RESUME.md,
docs/EVALUATION_0_19.md, docs/HANDOFF.md, governance/pending_sync.json before edits.

GitHub remains code/state authority; this candidate is not on GitHub until pending_sync is resolved.
The base is f163f5cc9e76d7f13cf5f48a373727f16d98b365 (PR26); development branch
feat/model-activation-resume-20260928. Do not modify main or auto-merge PR26.

Preserve read-only Drive sources, D: model/cache defaults, localhost+origin/remote authorization,
and memory-only OAuth tokens. Never register arbitrary checkpoints by extension alone.
Do not call structure verification GPU inference verification. Never use completion markers alone.
Resume unit is a completed task, not a denoising step. Keep provenance, immutable results,
and pending Windows/DriveFS/NVIDIA tests explicit. Do not claim unperformed tests or uploads.
