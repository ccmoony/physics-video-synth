# Physics Video Synth

This project generates realistic synthetic physical-interaction videos for 4D
reconstruction tests. The current scene, `ball_block_impact`, simulates a red
rubber ball impacting a textured wooden block on a room-scale hardwood floor.
Motion is simulated with PyBullet and rendered in Blender Cycles with Poly
Haven CC0 HDRI/PBR assets and ambientCG rubber PBR maps.

The current renderer builds a more lived-in room scene around the impact:
painted walls, baseboards, distant background objects, optional muted surface
marks, seed-controlled physical variation, fixed-camera rendering by default,
4K floor/block PBR texture sets with AO/roughness/normal/height maps,
UV-projected block faces, rubber normal/roughness/height detail on the ball,
calibrated focus distance, and final video noise/vignette/lens post-processing.

## Project Layout

- Source scripts: `scripts/`
- Downloaded CC0 render assets: `assets/polyhaven/`, `assets/ambientcg/`
- Optional local Blender install: `tools/blender-3.6.23-linux-x64/`
- Generated videos, previews, `.blend` files, transforms, and metadata: `renders/`

`assets/`, `tools/`, and `renders/` are local runtime/cache directories. They
are intentionally kept outside the source surface with `.gitignore`; regenerate
assets with `scripts/download_render_assets.py` and choose a fresh `--out-dir`
for new renders. Historical comparison renders can live under `renders/archive/`
without mixing into new batch output roots.

## Default Render Settings

- Duration: 8 seconds
- FPS: 24
- Frames: 192
- Resolution: 1280 x 720
- Renderer: Blender Cycles
- Samples: 32
- Physics: PyBullet, 12 substeps per rendered frame
- Visual assets: Poly Haven `brown_photostudio_05`, `wood_floor_worn`,
  `wood_table`; ambientCG `Rubber002` 4K
- Device: OptiX/GPU when available, CPU fallback otherwise

Suggested segmentation prompt: `ball.block`.

Each animation output directory contains `ball_block_impact.mp4`, a
baseline H.264/yuv420p compatibility video with constant 24 fps, faststart
metadata, and a silent AAC track. The same directory also receives the `.blend`,
ground-truth transforms, and scenario metadata.

## Re-render

Use Blender 3.6 LTS or a compatible Blender build with Cycles enabled. The
batch script resolves Blender in this order: explicit `--blender`, `BLENDER_BIN`,
`tools/blender-3.6.23-linux-x64/blender`, then `blender` on `PATH`. The render
script calls `python3` for the PyBullet simulation, so `pybullet` must be
available in that Python environment.

Install the Python runtime dependency:

```bash
python3 -m pip install -r physics-video-synth/requirements.txt
```

Download or refresh the local render assets. The render script requires these
Poly Haven and ambientCG assets so every render uses the same PBR inputs:

```bash
python3 physics-video-synth/scripts/download_render_assets.py
```

```bash
${BLENDER_BIN:-blender} -b \
  --python physics-video-synth/scripts/render_ball_block_impact.py -- \
  --mode animation \
  --out-dir physics-video-synth/renders/current_side_impact \
  --resolution 1280 720 \
  --fps 24 \
  --duration-sec 8 \
  --samples 32 \
  --device auto \
  --seed 7 \
  --motion side_impact \
  --block-texture-asset wood_table \
  --camera-jitter 0 \
  --surface-marks none
```

Use `--motion drop_onto_block` for a top-down drop where the ball falls onto
the wooden block instead of launching from the side. Add
`--drop-x-velocity 0.55 --drop-y-velocity 0.06` to give that falling ball a
small horizontal initial velocity. Use `--block-texture-asset stained_pine`
only when you explicitly want the warmer alternate block maps.

## Batch Render

For a quick smoke test, render several deterministic preview samples:

```bash
python3 physics-video-synth/scripts/batch_render_ball_block_impact.py \
  --out-root physics-video-synth/renders/batch_preview \
  --count 4 \
  --mode preview \
  --resolution 320 180 \
  --samples 8
```

For dataset-style videos, use animation mode:

```bash
python3 physics-video-synth/scripts/batch_render_ball_block_impact.py \
  --out-root physics-video-synth/renders/batch_animation \
  --count 16 \
  --mode animation \
  --resolution 1280 720 \
  --fps 24 \
  --duration-sec 8 \
  --samples 32 \
  --device auto \
  --motion side_impact \
  --block-texture-asset wood_table \
  --camera-jitter 0 \
  --surface-marks none
```

Each sample is written to `sample_0000/`, `sample_0001/`, etc. Every sample
contains its own video or preview, `.blend`, `ground_truth_transforms.json`, and
`scenario_metadata.json`. The batch root also contains `batch_manifest.json`
with seeds, output paths, and the exact Blender commands used.
