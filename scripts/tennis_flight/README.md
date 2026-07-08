# Tennis flight scene

This scene follows the same coupled simulate-render-build pipeline as `scripts/ramp_collision`:

1. `simulate_tennis_flight.py` runs PyBullet and writes a trajectory JSON.
2. `render_tennis_flight.py` builds the Blender scene, calls the simulator, applies the trajectories as object keyframes, exports `ground_truth_transforms.json`, and renders the video.
3. `batch_render_tennis_flight.py` orchestrates multiple renders.
4. `build_pcve_tennis_flight.py` builds a named PCVE benchmark suite.

## Quick render

```bash
python scripts/tennis_flight/batch_render_tennis_flight.py \
  --mode animation \
  --count 1 \
  --seed-base 3000 \
  --resolution 960 540 \
  --fps 24 \
  --duration-sec 6.0 \
  --samples 128 \
  --device cpu \
  --out-root renders/test_tennis_video
```

The output video is written to `renders/test_tennis_video/sample_0000/tennis_flight.mp4`.

## Single render

```bash
./tools/blender-3.6.23-linux-x64/blender -b \
  --python scripts/tennis_flight/render_tennis_flight.py -- \
  --mode animation \
  --out-dir renders/test_tennis_video \
  --resolution 960 540 \
  --fps 24 \
  --duration-sec 6.0 \
  --samples 64 \
  --device cpu \
  --seed 3000
```

The output video is written to `renders/test_tennis_video/tennis_flight.mp4`.

## Simulate only

```bash
python scripts/tennis_flight/simulate_tennis_flight.py \
  --out renders/test_tennis_physics.json \
  --fps 24 \
  --duration-sec 6.0
```

## Build PCVE suite

```bash
python scripts/tennis_flight/build_pcve_tennis_flight.py \
  --out-root renders/pcve_tennis_flight_suite \
  --resolution 1280 720 \
  --fps 24 \
  --duration-sec 3.0 \
  --samples 32 \
  --device auto
```
