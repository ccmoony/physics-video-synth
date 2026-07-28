from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WORKSPACE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_BLENDER = WORKSPACE_DIR / "tools" / "blender-3.6.23-linux-x64" / "blender"
RENDER_SCRIPT = WORKSPACE_DIR / "scripts" / "picnic_apple_ball" / "render_picnic_apple_ball.py"


@dataclass(frozen=True)
class RenderCase:
    case_id: str
    description: str
    seed: int
    apple_offset_x: float
    drop_height: float
    grass_friction: float


# The suite isolates what turns a straight-down apple drop into a rolling ball.
# The contact point between two spheres always lies on the line joining their
# centers, so a hit whose lever arm (the apple's horizontal offset from the
# ball's center) is zero pushes straight through the ball's center and imparts
# no spin -- the ball is just pressed into the grass and stays put. A non-zero
# offset makes the hit oblique, and the ball rolls; how far it then travels is
# gated by the grass friction. Drop height sets the impact energy. Everything
# else -- the models, their placement, the camera -- is identical between
# cases. Every outcome below (roll distance in metres) is verified directly
# against simulate_picnic_apple_ball.py.
CASES = (
    RenderCase(
        case_id="picnic_apple_ball_baseline",
        description="The intended shot: the apple falls off-center onto the top of the ball "
        "(offset 0.105 m), the oblique hit torques the ball into a right-to-left roll, and it "
        "travels ~0.54 m across the grass before friction brings it to rest.",
        seed=7,
        apple_offset_x=0.105,
        drop_height=1.3,
        grass_friction=0.25,
    ),
    RenderCase(
        case_id="picnic_apple_ball_centered_no_roll",
        description="The apple drops dead-center above the ball (offset 0.0 m): the force passes "
        "through the ball's center with no lever arm, so it imparts no spin -- the ball is pressed "
        "straight down and does not roll at all. The apple simply bounces off the top.",
        seed=8,
        apple_offset_x=0.0,
        drop_height=1.3,
        grass_friction=0.25,
    ),
    RenderCase(
        case_id="picnic_apple_ball_far_roll",
        description="A slightly larger lever arm (0.115 m) on much slicker, low-friction grass "
        "(0.12): the same kind of hit sends the ball rolling more than twice as far (~1.12 m) "
        "before it stops.",
        seed=9,
        apple_offset_x=0.115,
        drop_height=1.3,
        grass_friction=0.12,
    ),
    RenderCase(
        case_id="picnic_apple_ball_short_roll",
        description="The same off-center hit as the baseline, but on high-friction turf (0.60): "
        "the ball starts rolling yet the grass damps it quickly, so it only travels ~0.19 m "
        "before stopping.",
        seed=10,
        apple_offset_x=0.105,
        drop_height=1.3,
        grass_friction=0.60,
    ),
    RenderCase(
        case_id="picnic_apple_ball_high_drop",
        description="The apple falls from nearly twice the height (2.2 m): the harder impact "
        "carries more energy into the same off-center hit, and the ball rolls further (~0.84 m) "
        "than the baseline before coming to rest.",
        seed=11,
        apple_offset_x=0.105,
        drop_height=2.2,
        grass_friction=0.25,
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the picnic apple/ball PCVE suite.")
    parser.add_argument(
        "--out-root", type=Path, default=WORKSPACE_DIR / "renders" / "pcve_picnic_apple_ball_suite",
    )
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--resolution", nargs=2, type=int, default=(1280, 720))
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--duration-sec", type=float, default=4.5)
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--device", choices=("auto", "cpu"), default="auto")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def render_command(args: argparse.Namespace, case: RenderCase, case_dir: Path) -> list[str]:
    return [
        str(args.blender.expanduser().resolve()),
        "-b", "--python", str(RENDER_SCRIPT.resolve()), "--",
        "--mode", "animation",
        "--out-dir", str(case_dir.resolve()),
        "--resolution", str(int(args.resolution[0])), str(int(args.resolution[1])),
        "--fps", str(int(args.fps)),
        "--duration-sec", str(float(args.duration_sec)),
        "--samples", str(int(args.samples)),
        "--device", str(args.device),
        "--seed", str(int(case.seed)),
        "--apple-offset-x", str(float(case.apple_offset_x)),
        "--drop-height", str(float(case.drop_height)),
        "--grass-friction", str(float(case.grass_friction)),
    ]


def standardize_outputs(case_dir: Path) -> dict[str, str]:
    src = case_dir / "picnic_apple_ball.mp4"
    if not src.exists():
        matches = sorted(case_dir.glob("*.mp4"))
        if not matches:
            raise FileNotFoundError(f"No mp4 found in {case_dir}")
        src = matches[0]
    dst = case_dir / "video.mp4"
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    outputs = {
        "video": dst,
        "ground_truth": case_dir / "ground_truth_transforms.json",
        "scenario_metadata": case_dir / "scenario_metadata.json",
    }
    return {key: str(path.resolve()) for key, path in outputs.items() if path.exists()}


def main() -> None:
    args = parse_args()
    args.out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out_root / "pcve_manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "suite": "picnic_apple_ball",
        "description": "An apple dropped onto a soccer ball on a picnic lawn, varying the one "
        "thing that decides whether (and how far) the ball rolls: the off-center lever arm of "
        "the hit, plus the grass friction and drop height that scale the resulting roll.",
        "resolution": [int(args.resolution[0]), int(args.resolution[1])],
        "fps": int(args.fps),
        "duration_sec": float(args.duration_sec),
        "samples": int(args.samples),
        "cases": [],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    for case in CASES:
        case_dir = args.out_root / "cases" / case.case_id
        command = render_command(args, case, case_dir)
        record: dict[str, Any] = {
            "case_id": case.case_id,
            "description": case.description,
            "seed": case.seed,
            "apple_offset_x": case.apple_offset_x,
            "drop_height": case.drop_height,
            "grass_friction": case.grass_friction,
            "case_dir": str(case_dir.resolve()),
            "command": command,
            "status": "pending",
        }
        manifest["cases"].append(record)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        expected = case_dir / "video.mp4"
        if args.skip_existing and expected.exists():
            record["status"] = "skipped_existing"
            record["outputs"] = standardize_outputs(case_dir)
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            print(f"[suite] skip existing {case.case_id}")
            continue

        if args.dry_run:
            record["status"] = "dry_run"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            print(" ".join(command))
            continue

        case_dir.mkdir(parents=True, exist_ok=True)
        print(
            f"[suite] render {case.case_id} "
            f"(offset={case.apple_offset_x}, drop={case.drop_height}, friction={case.grass_friction})"
        )
        started = time.time()
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError:
            record["status"] = "failed"
            record["elapsed_sec"] = round(time.time() - started, 1)
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            raise
        record["elapsed_sec"] = round(time.time() - started, 1)
        record["outputs"] = standardize_outputs(case_dir)
        record["status"] = "completed"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"[suite] completed {case.case_id} in {record['elapsed_sec']:.1f}s")

    print(f"[suite] manifest={manifest_path.resolve()}")


if __name__ == "__main__":
    main()
