# A rubber ball hits a solid wood block

A red rubber ball is put into a wooden room with one thing in the middle of the
floor -- a solid pine block, roughly two hands wide, one hand deep, one hand
tall -- and made to hit it. There are two motions, and only two: the ball rolls
in from off-camera and strikes the block from the side (`side_impact`), or it
falls out of frame above and lands on the block's top (`drop_onto_block`).
After the initial push nothing else is scripted. Every subsequent motion --
whether the block skids, tips, spins, or barely moves; whether the ball rebounds
back, deflects, or sits on top -- comes out of the block's mass and friction
against the ball's mass, restitution, and approach.

The scene exists so PCVE free-motion recovery and 4D fitting have something to
generalize *to*. The room, the block, and the ball are deliberately plain:
one hardwood floor, one Cornell-box shell of matte walls and a baseboard, one
solid rectangular block, one solid sphere. There is no clutter in the physics
frame -- the "background clutter" in the render is decorative geometry outside
the ball/block's reach. Two objects, one contact event, one HDRI-plus-three-area-
lights lighting rig; the only free variables are the ones the physics reads.

## Files

- `simulate_ball_block_impact.py` -- PyBullet physics: floor plane, one box, one
  sphere. Reports per-frame pose and velocity for both bodies, plus a running
  minimum of the ball-floor and ball-block gaps as a sanity check that nothing
  interpenetrated. Called from the render script as a subprocess.
- `render_ball_block_impact.py` -- Blender scene builder and renderer. Samples a
  scenario (or accepts one via `--scenario-json`), builds the room and the two
  bodies, runs the PyBullet subprocess through `run_physics_simulation`, bakes
  the returned trajectory as linear-interpolated keyframes, renders, and writes
  ground truth. This is where the room, the PBR materials, the camera, and the
  lighting rig live.
- `batch_render_ball_block_impact.py` -- runs the renderer N times with
  successive seeds. Preview mode by default (one still per sample); pass
  `--mode animation` for full videos. Writes `batch_manifest.json`.
- `build_pcve_motion_suite.py` -- builds the named PCVE benchmark: one carried-
  over baseline plus five fresh cases (three `side_impact`, two
  `drop_onto_block`), each defined by an explicit scenario-overrides block so
  the physics is reproducible. Writes `suite_manifest.json`.

## Quick start

```bash
# Activate the environment that has PyBullet installed
conda activate physics

# Preview a single frame
./tools/blender-3.6.23-linux-x64/blender -b \
    --python scripts/ball_block/render_ball_block_impact.py -- \
    --mode preview \
    --out-dir renders/ball_block_preview \
    --resolution 960 540 \
    --samples 64 \
    --device auto

# Render the full animation (side impact is the default motion)
./tools/blender-3.6.23-linux-x64/blender -b \
    --python scripts/ball_block/render_ball_block_impact.py -- \
    --mode animation \
    --out-dir renders/ball_block \
    --resolution 1920 1080 \
    --fps 24 \
    --duration-sec 8.0 \
    --samples 128 \
    --device auto

# Render the drop-onto-block motion
./tools/blender-3.6.23-linux-x64/blender -b \
    --python scripts/ball_block/render_ball_block_impact.py -- \
    --mode animation --motion drop_onto_block \
    --out-dir renders/ball_block_drop \
    --resolution 1920 1080 --samples 128 --device auto
```

## Simulate only

Physics runs on its own without opening Blender, so aims and material choices
can be checked in seconds:

```bash
python scripts/ball_block/simulate_ball_block_impact.py \
    --out /tmp/ball_block.json \
    --ball-initial-location -3.05 -0.12 0.341 \
    --ball-initial-velocity 6.0 0.0 0.0 \
    --block-location 0.23 -0.02 0.35
```

The output JSON is the same one the render script consumes internally. It
carries every frame's ball/block pose and velocity plus a `quality` block with
`min_ball_floor_gap` and `min_ball_block_gap` -- both should stay slightly
positive; a negative value means something interpenetrated.

## Motions

- **`side_impact`** (default). The ball starts near `(-3.15, -0.12, r)` and is
  launched east at `6 m/s`, spinning to match, and rolls across the floor into
  the block's western face. The ball is *already* rolling at launch; if it were
  launched with zero angular velocity it would skid for the first tenth of a
  second while friction spun it up, which reads as a shove rather than a roll.
- **`drop_onto_block`**. The ball starts about 1.32 m above the block's top
  with a small horizontal drift, falls, and lands on the block. The default
  drift is randomized within a narrow envelope; `--drop-x-velocity` /
  `--drop-y-velocity` pin it to a specific value for reproducible framings.

Both motions target the same block at roughly `(0.23, -0.02, 0.35)` with the
same `(0.92, 0.58, 0.70)` dimensions, and use the same materials, camera family,
and lights. The scenario differs by camera framing (`side_impact` sits back and
low; `drop_onto_block` moves the camera up and forward and re-aims it at the
block's top), by ball launch state, and by nothing else.

## Randomization and repeatability

`--seed` is the only randomness source: everything the RNG touches -- block
position and yaw within a small envelope, ball launch state within a small
envelope, all masses and friction/restitution values within physical bands,
camera pose and lens jitter, all light powers, sizes, and colors, per-frame
camera jitter, ball scuff pattern, and colour palette wobble -- is drawn from
`random.Random(seed)`. `--physics-jitter` scales all physical randomization
uniformly (`0.0` = pin to the nominal case), and `--camera-jitter` scales the
per-frame camera shake. To reproduce a specific rendered case exactly, feed its
`scenario_metadata.json` back in through `--scenario-json`, or drop a partial
override on top of a sampled scenario with `--scenario-overrides-json`.

## PCVE motion suite

```bash
python scripts/ball_block/build_pcve_motion_suite.py \
    --out-root renders/pcve_general_motion_suite \
    --resolution 1280 720 --fps 24 --samples 32 --device auto
```

Cases (all use the `wood_table` block texture and share resolution/fps/samples):

| case_id | motion | intent |
|---|---|---|
| `existing_side_impact_wood_table` | side_impact | Carried-over baseline; not re-rendered -- copied out of the archive. |
| `side_moderate_head_on` | side_impact | Head-on ground rolling contact, fully visible from the camera. |
| `side_oblique_moderate` | side_impact | Mild oblique velocity plus a block yaw so the contact is off-axis. |
| `side_slow_graze` | side_impact | Lower-energy shallow lateral graze; grazing, not centred. |
| `drop_centered_soft` | drop_onto_block | Free fall nearly centred on the block. |
| `drop_lateral_mild` | drop_onto_block | Free fall with lateral drift so the landing is off-centre. |

Each case is defined by an explicit `scenario_overrides` block in
`build_pcve_motion_suite.py`; the renderer receives it via
`--scenario-overrides-json` and every physical parameter that matters
(masses, frictions, restitutions, launch state, block position and yaw) is
pinned rather than sampled. `physics_jitter` and `camera_jitter` default to
`0.0` in this driver for the same reason.

Stale case directories left over from earlier revisions of the suite are
removed at the start of each run; pass `--keep-stale-cases` to keep them, and
`--skip-existing` to leave finished cases alone (they are validated against
`CANONICAL_BLOCK_TEXTURE` and re-rendered if the texture drifted).

## Physics notes

- **Rolling resistance sits on the ball, not on the floor.** The scene's ball
  and block both carry a small `rollingFriction` and `spinningFriction`; the
  floor is a plain `GEOM_PLANE` with `lateralFriction` and `restitution=0`.
  Combined damping is enough to bring the block to rest within the 8 s window
  after a side impact and to arrest the ball's post-contact spin on the block's
  top face after a drop.
- **Bullet multiplies the two bodies' restitutions.** The nominal side-impact
  sees `ball_restitution=0.78` against `block_restitution=0.55`, so the
  effective rebound coefficient is `0.429`. Both values are exposed separately
  because they are properties of two different objects, and both are written
  into the ground truth.
- **The physics step is fine.** `substeps=12` at 24 fps means `dt ≈ 3.5 ms`;
  the solver runs 180 iterations. This is what the drop's landing needs to
  stay quiet -- coarser stepping produces a visible jitter as the ball settles
  onto the block's top face.
- **Interpenetration is monitored, not prevented.** `sphere_box_gap` computes
  the true signed distance between the sphere and the box (respecting the
  block's yaw) every frame; `min_ball_block_gap` and `min_ball_floor_gap` land
  in the `quality` block of the sim's output. Either value going meaningfully
  negative is a sign that the solver missed the contact.

## Key parameters

All physics parameters can be overridden via `--scenario-overrides-json`, or
directly in the `NEW_CASES` overrides in `build_pcve_motion_suite.py`.

- `ball_initial_location` / `ball_initial_velocity`: what the push is. Nominal
  side impact is `(-3.15, -0.12, r)` at `(6.0, 0.0, 0.0) m/s`; nominal drop is
  `(block_x - 0.16, block_y, block_top + r + 1.32)` at `(0.18, 0.0, -0.18) m/s`.
- `block_location`, `block_yaw_deg`: where the block sits. Default centre is
  `(0.23, -0.02, 0.35)` at yaw 0; the suite's oblique cases yaw it a few
  degrees so the contact normal is not axis-aligned.
- `ball_mass` / `block_mass`: default `0.58` / `0.65` kg. Mass ratio close to
  one means the block reliably reacts to the impact rather than absorbing it.
- `floor_friction`: default `0.82`. High enough that the block skids only a
  short distance after being hit and the ball's post-contact motion damps
  rather than sliding away.
- `ball_friction` / `ball_restitution`: default `0.38` / `0.78`.
- `block_friction` / `block_restitution`: default `0.32` / `0.55`.

## Outputs

- `ball_block_impact.mp4` -- rendered video (animation mode). Passed through an
  ffmpeg post stage that adds mild lens distortion, film grain, a vignette, a
  colour/gamma tweak and a light unsharp mask; disable with
  `--no-video-postprocess`.
- `preview_frame_NNNNN.png` -- preview still (preview mode; frame selected by
  `--preview-frame`, default 86).
- `ball_block_impact.blend` -- saved Blender scene.
- `ground_truth_transforms.json` -- per-frame world matrices, locations, and
  velocities for the ball and block, the camera world matrix and its inverse,
  the ball-floor and ball-block gaps, plus `objects` (radius and dimensions),
  `camera` (lens, sensor width, resolution), and `physics` (the full parameter
  set the simulation was run with).
- `scenario_metadata.json` -- the scenario the render was built from: seed,
  motion, physics parameters, render/lighting/material choices, camera pose.
  Feed it back in via `--scenario-json` to reproduce the render bit-for-bit.
- `scenario_overrides.json` (suite runs only) -- the overrides applied on top
  of the sampled scenario for this case.
