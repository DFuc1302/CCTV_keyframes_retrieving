# Reusable multimodal video search

This repository contains a reusable university project for processing video,
building multimodal indexes and serving search results. Generated media,
models, databases, FAISS indexes and Redis data are kept outside the Git
checkout.

## Repository map

```text
app/                  FastAPI search application and runtime services
pipeline/             Ingest, frames, dedupe, merge, OCR, ASR and indexing
static/               Browser assets
benchmark/            Evaluation notes and frozen qrels workflow
docs/                 Architecture, pipeline map and maintenance notes
legacy/aic-2026/      Retired competition, Colab and submission utilities
requirements/         Runtime, pipeline and development dependency sets
```

The processing flow is:

```text
videos -> manifest -> frames -> dedupe -> merge
       -> OCR + ASR -> bundle/index -> validation -> search API
```

## Start with the documentation

- [CCTV demo acceptance](docs/CCTV_DEMO_ACCEPTANCE.md) — repeatable local HTTP integration checks and latency after event/dispatch UI integration.
- [CCTV simulated dispatch UI](docs/CCTV_DISPATCH_UI.md) — read-only proposals, personnel/reserve limits and current review/input guards.
- [CCTV event benchmark tools](docs/CCTV_BENCHMARK.md) — annotation/split validation, exact temporal-IoU matching and explicitly synthetic metric fixtures.
- [CCTV simulated allocation proposals](docs/CCTV_DISPATCH.md) — exact bounded solver, review-bound demands and unmet explanations; no real dispatch actions.
- [CCTV simulated patrol resources](docs/CCTV_RESOURCES.md) — fictional zones/teams, personnel caps, reserves and directed travel constraints.
- [CCTV temporal incident screening](docs/CCTV_INCIDENTS.md) — scoped move-then-stop rule, synthetic confounders and independent evaluation plan.
- [CCTV event evidence/review](docs/CCTV_EVENTS.md) — immutable candidate packages, explicit synthetic demo and auditable review CLI.
- [CCTV traffic API/UI](docs/CCTV_TRAFFIC_UI.md) — camera/video metrics, ROI reference images and evidence timeline.
- [CCTV traffic windows](docs/CCTV_CONGESTION.md) — draft ROI demo, CPU metrics, persistence and Windows commands.
- [CCTV native video tracking](docs/CCTV_TRACKING.md) — ByteTrack baseline, native PTS, segment resume and Windows commands.
- [Vietnamese university project handover](docs/KE_THUA_VA_PHAT_TRIEN_DO_AN.md) — setup, reuse boundaries, proposed scope and an eight-week development plan.
- [Architecture](docs/ARCHITECTURE.md) — application and browser structure.
- [Pipeline map](docs/PIPELINE_MAP.md) — stage inputs, outputs and contracts.
- [Pipeline guide](pipeline/README.md) — packaged stage entry points.
- [Requirements](requirements/README.md) — install only what the task needs.
- [Cleanup guide](docs/CLEANUP_GUIDE.md) — rules for keeping generated data
  out of Git.

Historical AIC 2026 documents are indexed under
[`docs/archive/aic-2026/`](docs/archive/aic-2026/). They are reference material
and are not part of the reusable runtime.

## Run the pipeline

Use a data directory outside the repository and pass its paths through the
stage arguments or environment variables. The compatibility launchers remain
available:

```bash
python -m pipeline.run --help
python -m pipeline.new_dataset --help
python run_s01_pipeline.py --help
python run_new_dataset_pipeline.py --help
```

Run individual stages from their package when debugging, for example:

```bash
python -m pipeline.frames.extract --help
python -m pipeline.ocr.index --help
python -m pipeline.asr.index --help
python -m pipeline.index.bundle --help
```

## Run the application

The implementation is [app/main.py](app/main.py). `main.py` remains a small
compatibility wrapper for existing launchers. Install the appropriate
dependency set, configure the external data paths, then start the existing
launcher for the target environment.

## Validate changes

```bash
python -m compileall -q .
python -m unittest discover -v
```

See [docs/BASELINE_VALIDATION.md](docs/BASELINE_VALIDATION.md) for the current
test status and optional dependency requirements.
