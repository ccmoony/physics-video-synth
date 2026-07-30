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
RENDER_SCRIPT = WORKSPACE_DIR / "scripts" / "ball_box_rebound" / "render_ball_box_rebound.py"


@dataclass(frozen=True)
class RenderCase:
    case_id: str
    description: str
    seed: int
    overrides: dict[str, Any]
    duration_sec: float | None = None


# The suite is built on one fact that is easy to state and easy to get wrong:
# a bounce off a wall scales the *normal* component of the ball's velocity and
# leaves the tangential one alone, so changing how lively the wall is changes
# the direction the ball comes off it, not just how fast.
#
# Four cases hold the push fixed at 2.60 m/s and vary only the toy chest's
# restitution. Every one of them meets the chest's panel at exactly the same
# point, at exactly the same 2.36 m/s, at exactly the same 30 deg. What changes
# is the angle they leave at -- 29 deg, 36 deg, 42 deg, 61 deg -- and in the last
# two that flatter rebound runs along the front of the chest and past the target
# ball entirely. A model that reads "less bouncy" as "same path, slower" gets
# the wrong answer for every case except the hero.
#
# The fifth case is the distractor, and it is why the suite is worth rendering.
# It puts the hero's chest back and softens the push instead. The rebound angle
# is unchanged (30.1 deg against the hero's 29.2 -- speed does not bend it), the
# ball still reaches the target, and the target still ends up moving about
# 0.12 m -- the same outcome as `clips_target`, from a completely different
# cause. The resting positions do not separate them. Only the speed of the
# approach, and the angle the ball leaves the chest at, do.
#
# All values are tuned against simulate_ball_box_rebound.py and the outcomes are
# emergent, not scripted.
CASES = (
    RenderCase(
        case_id="ball_box_rebound_knocks_target",
        description="The hero case. A 2.60 m/s roll into the toy chest's front panel at 30 deg "
        "off the normal; a lively chest sends it back out at 29 deg, across the front of the box "
        "and into the little football, which is knocked 0.30 m clear.",
        seed=11001,
        overrides={"physics": {"chest_restitution": 0.80, "launch_speed": 2.60}},
    ),
    RenderCase(
        case_id="ball_box_rebound_clips_target",
        description="A duller chest (0.62). Same approach, but the rebound comes off at 36 deg "
        "instead of 29 -- flat enough that the ball only clips the football on the way past. It "
        "moves 0.12 m and the rolling ball carries on to the far side.",
        seed=11002,
        overrides={"physics": {"chest_restitution": 0.62, "launch_speed": 2.60}},
    ),
    RenderCase(
        case_id="ball_box_rebound_misses_target",
        description="Half-lively chest (0.48). The rebound flattens to 42 deg, which is enough to "
        "carry the ball wide of the football altogether. Nothing else in the scene changed: same "
        "push, same contact point, same 2.36 m/s into the panel.",
        seed=11003,
        overrides={"physics": {"chest_restitution": 0.48, "launch_speed": 2.60}},
        duration_sec=3.0,
    ),
    RenderCase(
        case_id="ball_box_rebound_dead_box",
        description="A dead chest (0.22). Almost none of the normal component survives, so the "
        "ball leaves at 61 deg and skims away along the front of the box rather than back across "
        "the room -- it ends up on the far side of the chest without ever going near the football.",
        seed=11004,
        overrides={"physics": {"chest_restitution": 0.22, "launch_speed": 2.60}},
        duration_sec=3.0,
    ),
    RenderCase(
        case_id="ball_box_rebound_soft_push",
        description="The distractor: the hero's chest, but a gentle 2.15 m/s push. The bounce "
        "angle is unchanged at 30 deg -- restitution bends the rebound, speed does not -- so the "
        "ball still arrives at the football, just slowly, and nudges it 0.12 m. That is the same "
        "result as `clips_target` for an entirely different reason, and only the approach speed "
        "and the rebound angle tell them apart.",
        seed=11005,
        overrides={"physics": {"chest_restitution": 0.80, "launch_speed": 2.15}},
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a PCVE synthetic ball-off-toy-chest benchmark suite.",
    )
    parser.add_argument(
        "--out-root", type=Path,
        default=WORKSPACE_DIR / "renders" / "pcve_ball_box_rebound_suite",
    )
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--resolution", nargs=2, type=int, default=(1280, 720))
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument(
        "--duration-sec", type=float, default=2.8,
        help="Default clip length. Cases whose ball runs on past the chest instead of stopping "
        "against the target override this with their own value.",
    )
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--device", choices=("auto", "cpu"), default="auto")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--verbose-render", action="store_true",
        help="Stream the full Blender render log instead of only suite progress.",
    )
    return parser.parse_args()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def render_command(args, case: RenderCase, *, case_dir: Path, overrides_path: Path) -> list[str]:
    duration = case.duration_sec if case.duration_sec is not None else float(args.duration_sec)
    return [
        str(args.blender.expanduser().resolve()),
        "-b", "--python", str(RENDER_SCRIPT.resolve()), "--",
        "--mode", "animation",
        "--out-dir", str(case_dir.resolve()),
        "--resolution", str(int(args.resolution[0])), str(int(args.resolution[1])),
        "--fps", str(int(args.fps)),
        "--duration-sec", str(float(duration)),
        "--samples", str(int(args.samples)),
        "--device", str(args.device),
        "--seed", str(int(case.seed)),
        "--scenario-overrides-json", str(overrides_path.resolve()),
    ]


def standardize_render_outputs(case_dir: Path) -> dict[str, str]:
    video_source = case_dir / "ball_box_rebound.mp4"
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


def case_outcome(case_dir: Path) -> dict[str, Any]:
    """Pull the physics quality block back out of the rendered ground truth.

    The suite exists to compare *outcomes*, so the manifest records what each
    case actually did -- what angle the ball came off the chest at, whether it
    reached the target ball, how far the target went -- rather than only the
    restitution it was asked for.
    """
    gt = json.loads((case_dir / "ground_truth_transforms.json").read_text(encoding="utf-8"))
    return gt.get("physics", {}).get("quality", {})


def tail(text: str | None, *, max_lines: int = 80) -> str:
    return "\n".join((text or "").splitlines()[-max_lines:])


def run_render(command: list[str], *, verbose: bool) -> None:
    if verbose:
        subprocess.run(command, check=True)
        return
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode == 0:
        return
    print("[suite] render failed; stdout tail:")
    print(tail(result.stdout))
    print("[suite] render failed; stderr tail:")
    print(tail(result.stderr))
    raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)


def main() -> None:
    args = parse_args()
    args.out_root.mkdir(parents=True, exist_ok=True)

    manifest_path = args.out_root / "suite_manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "suite_name": "pcve_ball_box_rebound_suite",
        "description": "The same ball rolled at the same toy chest from the same place, varying "
        "only how lively the chest is or how hard the ball was pushed. Because a bounce scales "
        "the normal component of the velocity and not the tangential one, the restitution cases "
        "differ in the *direction* the ball leaves the chest, and that decides whether it reaches "
        "the second ball at all. The soft-push case reproduces one of those outcomes without "
        "changing the angle.",
        "resolution": [int(args.resolution[0]), int(args.resolution[1])],
        "fps": int(args.fps),
        "default_duration_sec": float(args.duration_sec),
        "samples": int(args.samples),
        "cases": [],
    }
    write_json(manifest_path, manifest)

    for case in CASES:
        case_dir = args.out_root / "cases" / case.case_id
        overrides_path = case_dir / "scenario_overrides.json"
        write_json(overrides_path, case.overrides)

        command = render_command(args, case, case_dir=case_dir, overrides_path=overrides_path)
        record: dict[str, Any] = {
            "case_id": case.case_id,
            "description": case.description,
            "seed": int(case.seed),
            "duration_sec": case.duration_sec if case.duration_sec is not None else float(args.duration_sec),
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
            record["outcome"] = case_outcome(case_dir)
            write_json(manifest_path, manifest)
            print(f"[suite] skip existing {case.case_id}")
            continue

        if args.dry_run:
            record["status"] = "dry_run"
            write_json(manifest_path, manifest)
            print(" ".join(command))
            continue

        case_dir.mkdir(parents=True, exist_ok=True)
        start = time.perf_counter()
        print(f"[suite] render {case.case_id}")
        try:
            run_render(command, verbose=bool(args.verbose_render))
        except subprocess.CalledProcessError:
            record["status"] = "failed"
            record["elapsed_sec"] = round(time.perf_counter() - start, 3)
            write_json(manifest_path, manifest)
            raise

        record["outputs"] = standardize_render_outputs(case_dir)
        record["outcome"] = case_outcome(case_dir)
        record["elapsed_sec"] = round(time.perf_counter() - start, 3)
        record["status"] = "completed"
        write_json(manifest_path, manifest)
        print(f"[suite] completed {case.case_id} in {record['elapsed_sec']:.1f}s")

    print(f"[suite] manifest={manifest_path.resolve()}")


if __name__ == "__main__":
    main()
