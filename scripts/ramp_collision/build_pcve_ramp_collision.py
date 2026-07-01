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
RENDER_SCRIPT = WORKSPACE_DIR / "scripts" / "ramp_collision" / "render_ramp_collision.py"


@dataclass(frozen=True)
class RenderCase:
    case_id: str
    description: str
    seed: int
    overrides: dict[str, Any]


CASES = (
    RenderCase(
        case_id="ramp_baseline",
        description="Baseline ramp collision with default physics.",
        seed=2001,
        overrides={},
    ),
    RenderCase(
        case_id="ramp_high_restitution",
        description="Higher ball restitution leads to more bounce after impact.",
        seed=2002,
        overrides={
            "physics": {
                "ball_restitution": 0.78,
                "marble_restitution": 0.45,
            },
        },
    ),
    RenderCase(
        case_id="ramp_heavy_ball",
        description="Heavier falling marble carries more momentum into the stationary marbles.",
        seed=2003,
        overrides={
            "physics": {
                "ball_mass": 0.08,
                "ball_friction": 0.35,
            },
        },
    ),
    RenderCase(
        case_id="ramp_steep",
        description="Steeper ramp angle increases the impact speed.",
        seed=2004,
        overrides={
            "physics": {
                "ramp_angle_deg": 16.0,
            },
        },
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a PCVE synthetic ramp collision benchmark suite."
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=WORKSPACE_DIR / "renders" / "pcve_ramp_collision_suite",
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
    video_source = case_dir / "ramp_collision.mp4"
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
        "suite_name": "pcve_ramp_collision_suite",
        "description": "Ramp collision cases with PyBullet-driven trajectories for PCVE evaluation.",
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
