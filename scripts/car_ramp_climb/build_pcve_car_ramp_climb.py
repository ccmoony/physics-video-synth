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
RENDER_SCRIPT = WORKSPACE_DIR / "scripts" / "car_ramp_climb" / "render_car_ramp_climb.py"


@dataclass(frozen=True)
class RenderCase:
    case_id: str
    description: str
    seed: int
    overrides: dict[str, Any]


# The suite spans the distinct *outcomes* of the same car on the same ramp,
# driven by two knobs: the ramp surface's friction and the launch push. The
# first three cases hold the push fixed (launch_speed=2.6) and vary only the
# surface, mirroring the reference photo's side-by-side ramp materials; the
# last two hold the slick grey-asphalt surface fixed and vary the push, to
# isolate the two speed-driven failure modes the surface sweep doesn't show on
# its own. All values are empirically tuned via simulate_car_ramp_climb.py so
# every outcome is real emergent physics, and each case pins its launch_speed
# explicitly -- the single-scene default in render_car_ramp_climb.py is 2.7
# (tuned for a clean bottom-down landing that stays in the hero framing), which
# this suite must not inherit.
#
# Why speed, not surface, for the two failure-mode cases: an in-frame
# "stalls near the top then slides back" only happens on a medium-friction
# surface (turf) -- on a slick ramp the car retains enough speed to slide all
# the way back down and off the right of frame -- and the "clears the top but
# under-rotates onto its roof" flip only happens in a moderate-friction /
# moderate-speed band (verified: grey/0.25 flips at 2.5, lands upright again by
# 2.7-2.8). So both failure modes live on their natural surface, tuned by push.
CASES = (
    RenderCase(
        case_id="car_ramp_climb_grip_orange",
        description="High-friction orange grip-tape surface: the car barely climbs before "
        "friction stops it, and it stays right where it stopped.",
        seed=13001,
        overrides={
            "surface": "grip_orange",
            "physics": {"ramp_friction": 0.9, "launch_speed": 2.6},
        },
    ),
    RenderCase(
        case_id="car_ramp_climb_stall_slideback",
        description="Medium-friction artificial turf, push too weak to crest the ramp: the car "
        "climbs to just below the top edge, stalls there, then slides back down under gravity "
        "and settles near where it started -- all within frame.",
        seed=13002,
        overrides={
            "surface": "turf_green",
            "physics": {"ramp_friction": 0.5, "launch_speed": 2.6},
        },
    ),
    RenderCase(
        case_id="car_ramp_climb_underrotate_flip",
        description="Slick grey asphalt, moderate push: the car keeps enough speed to launch "
        "off the top of the ramp, but not enough to finish rotating in the air, so it comes down "
        "inverted and lands on its roof just past the ramp's high end.",
        seed=13003,
        overrides={
            "surface": "asphalt_grey",
            "physics": {"ramp_friction": 0.25, "launch_speed": 2.5},
        },
    ),
    RenderCase(
        case_id="car_ramp_climb_clear_land",
        description="The same slick grey asphalt with a firmer push: now the car clears the top "
        "with enough speed to complete its rotation in the air and land cleanly bottom-down "
        "(the successful counterpart to the roof-landing flip case).",
        seed=13005,
        overrides={
            "surface": "asphalt_grey",
            "physics": {"ramp_friction": 0.25, "launch_speed": 2.8},
        },
    ),
    RenderCase(
        case_id="car_ramp_climb_asphalt_dark",
        description="Very low-friction dark asphalt/rubber at the same 2.6 push as the surface "
        "sweep: the slickest ramp carries the car off the top with the most speed to spare, so "
        "it flies furthest through the air before landing upright well beyond the ramp.",
        seed=13004,
        overrides={
            "surface": "asphalt_dark",
            "physics": {"ramp_friction": 0.12, "launch_speed": 2.6},
        },
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a PCVE synthetic car-ramp-climb benchmark suite."
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=WORKSPACE_DIR / "renders" / "pcve_car_ramp_climb_suite",
    )
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--resolution", nargs=2, type=int, default=(1280, 720))
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--duration-sec", type=float, default=3.0)
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--device", choices=("auto", "cpu"), default="auto")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--verbose-render",
        action="store_true",
        help="Stream the full Blender render log instead of showing only suite progress.",
    )
    return parser.parse_args()


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def render_command(
    args: argparse.Namespace,
    case: RenderCase,
    *,
    case_dir: Path,
    overrides_path: Path,
) -> list[str]:
    return [
        str(args.blender.expanduser().resolve()),
        "-b",
        "--python",
        str(RENDER_SCRIPT.resolve()),
        "--",
        "--mode",
        "animation",
        "--out-dir",
        str(case_dir.resolve()),
        "--resolution",
        str(int(args.resolution[0])),
        str(int(args.resolution[1])),
        "--fps",
        str(int(args.fps)),
        "--duration-sec",
        str(float(args.duration_sec)),
        "--samples",
        str(int(args.samples)),
        "--device",
        str(args.device),
        "--seed",
        str(int(case.seed)),
        "--scenario-overrides-json",
        str(overrides_path.resolve()),
    ]


def standardize_render_outputs(case_dir: Path) -> dict[str, str]:
    video_source = case_dir / "car_ramp_climb.mp4"
    if not video_source.exists():
        matches = sorted(case_dir.glob("*.mp4"))
        if not matches:
            raise FileNotFoundError(f"No mp4 found in {case_dir}")
        video_source = matches[0]
    video_target = case_dir / "video.mp4"
    if video_source.resolve() != video_target.resolve():
        shutil.copy2(video_source, video_target)

    outputs = {
        "video": video_target,
        "ground_truth": case_dir / "ground_truth_transforms.json",
        "scenario_metadata": case_dir / "scenario_metadata.json",
        "scenario_overrides": case_dir / "scenario_overrides.json",
    }
    for key, path in outputs.items():
        if not path.exists():
            raise FileNotFoundError(f"Missing rendered {key}: {path}")
    return {key: str(path.resolve()) for key, path in outputs.items()}


def tail(text: str | None, *, max_lines: int = 80) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


def run_render(command: list[str], *, verbose: bool) -> None:
    if verbose:
        subprocess.run(command, check=True)
        return

    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode == 0:
        return

    print("[suite] render command failed; stdout tail:")
    print(tail(result.stdout))
    print("[suite] render command failed; stderr tail:")
    print(tail(result.stderr))
    raise subprocess.CalledProcessError(
        result.returncode,
        command,
        output=result.stdout,
        stderr=result.stderr,
    )


def main() -> None:
    args = parse_args()
    args.out_root.mkdir(parents=True, exist_ok=True)

    manifest_path = args.out_root / "suite_manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "suite_name": "pcve_car_ramp_climb_suite",
        "description": "Toy car given an identical push up an identical ramp, with only the "
        "surface material (and its real-world friction) changed across the four cases -- a "
        "direct synthetic instance of the 'same action, different surface, different outcome' "
        "friction-comparison scenario for PCVE evaluation.",
        "resolution": [int(args.resolution[0]), int(args.resolution[1])],
        "fps": int(args.fps),
        "duration_sec": float(args.duration_sec),
        "samples": int(args.samples),
        "cases": [],
    }
    write_json(manifest_path, manifest)

    for case in CASES:
        case_dir = args.out_root / "cases" / case.case_id
        overrides_path = case_dir / "scenario_overrides.json"
        write_json(overrides_path, case.overrides)

        command = render_command(
            args,
            case,
            case_dir=case_dir,
            overrides_path=overrides_path,
        )
        record: dict[str, Any] = {
            "case_id": case.case_id,
            "kind": "rendered",
            "description": case.description,
            "seed": int(case.seed),
            "case_dir": str(case_dir.resolve()),
            "scenario_overrides_json": str(overrides_path.resolve()),
            "command": command,
            "status": "pending",
        }
        manifest["cases"].append(record)
        write_json(manifest_path, manifest)

        expected_video = case_dir / "video.mp4"
        if args.skip_existing and expected_video.exists():
            record["status"] = "skipped_existing"
            record["outputs"] = standardize_render_outputs(case_dir)
            write_json(manifest_path, manifest)
            print(f"[suite] skip existing {case.case_id}")
            continue

        if args.dry_run:
            record["status"] = "dry_run"
            write_json(manifest_path, manifest)
            print(" ".join(command))
            continue

        case_dir.mkdir(parents=True, exist_ok=True)
        start_time = time.perf_counter()
        print(f"[suite] render {case.case_id}")
        try:
            run_render(command, verbose=bool(args.verbose_render))
        except subprocess.CalledProcessError:
            record["status"] = "failed"
            record["elapsed_sec"] = round(time.perf_counter() - start_time, 3)
            write_json(manifest_path, manifest)
            raise

        record["outputs"] = standardize_render_outputs(case_dir)
        record["elapsed_sec"] = round(time.perf_counter() - start_time, 3)
        record["status"] = "completed"
        write_json(manifest_path, manifest)
        print(f"[suite] completed {case.case_id} in {record['elapsed_sec']:.1f}s")

    print(f"[suite] manifest={manifest_path.resolve()}")


if __name__ == "__main__":
    main()
