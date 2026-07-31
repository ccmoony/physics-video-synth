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
RENDER_SCRIPT = (WORKSPACE_DIR / "scripts" / "table_marble_collision"
                 / "render_table_marble_collision.py")


@dataclass(frozen=True)
class RenderCase:
    case_id: str
    description: str
    seed: int
    overrides: dict[str, Any]
    duration_sec: float | None = None


# The suite is built on one fact that is easy to state and easy to get wrong:
# **the mass ratio of two balls of the same material is the cube of their size
# ratio, and it is the mass ratio that decides what a collision does.**
#
# Four cases hold everything fixed -- same big marble, same 1.31 m/s push from
# the same place, same aim, so every one of them arrives at the struck marble at
# 0.83-0.88 m/s -- and change only how big the struck marble is. Doubling its
# diameter multiplies its mass by eight, and the outcome runs through the whole
# range a two-body impact has to offer:
#
#   - struck ball an eighth of the mass: it leaves at 1.5x the speed the big one
#     arrived at, and the big one barely notices, keeping 80 per cent of its own;
#   - a third of the mass: 1.3x, and the big one is down to 55 per cent;
#   - equal mass: the big one stops almost dead, and the two leave 45 deg apart
#     rather than nearly in line;
#   - struck ball 2.7 times the mass: the big one comes *back*, and the pair
#     leaves at 164 deg.
#
# A model that reads "bigger ball, therefore harder to move, therefore
# everything happens a bit less" gets the direction of the big marble wrong in
# the last two cases and the *speed ordering* wrong in the first.
#
# The fifth case is the distractor, and it is why the suite is worth rendering.
# It restores the hero's pair of marbles and softens the push instead. The struck
# marble then travels 0.12 m, near enough the same as `heavier_target`'s 0.09 -- but
# for a completely different reason, and the resting positions barely separate
# them. What does is the big marble: in `heavier_target` it reverses, and here it
# carries on the way it was going.
#
# All values are tuned against simulate_table_marble_collision.py and the
# outcomes are emergent, not scripted.
CASES = (
    RenderCase(
        case_id="table_marble_throws_it_clear",
        description="The hero case. A 100 mm glass marble rolled 0.62 m along the bar table into "
        "a 50 mm one -- eight times lighter, because it is half the diameter and the same glass. "
        "It arrives at 0.83 m/s and the small marble leaves at 1.29 m/s, half again as fast as "
        "anything in the shot was moving, while the big one keeps 80 per cent of its speed and "
        "follows it up the table. The small one runs 0.68 m, the big one 0.27 m after the "
        "contact, and they come to rest 0.46 m apart.",
        seed=17001,
        overrides={"physics": {"ball_b_radius": 0.025, "launch_speed": 1.31}},
    ),
    RenderCase(
        case_id="table_marble_middling_ratio",
        description="A 70 mm struck marble: 2.9 times lighter rather than 8. Same push, same "
        "approach speed. It leaves at 1.29x instead of 1.55x, and the big marble is now down to 55 "
        "per cent of its speed -- visibly checked by the impact rather than sailing through it. "
        "The two run 0.48 m and 0.15 m and end 0.39 m apart.",
        seed=17002,
        overrides={"physics": {"ball_b_radius": 0.035, "launch_speed": 1.31}},
    ),
    RenderCase(
        case_id="table_marble_matched_pair",
        description="Two identical 100 mm marbles. The textbook case: the moving one hands over "
        "nearly all of its speed and stops where it stands -- 0.155x, which is 0.13 m/s -- and "
        "because the impact is 6.9 deg off centre the two leave 45 deg apart instead of nearly in "
        "line. Same push, same aim, same approach speed as the hero. The struck marble takes "
        "0.87x and runs 0.23 m.",
        seed=17003,
        overrides={"physics": {"ball_b_radius": 0.050, "launch_speed": 1.31}},
    ),
    RenderCase(
        case_id="table_marble_heavier_target",
        description="A 140 mm struck marble, 2.7 times *heavier* than the one that hits it. The "
        "big marble bounces back the way it came at 0.33x its approach speed, retreating 0.075 m "
        "from the contact point, and the pair leaves 164 deg apart. The struck marble barely "
        "moves: 0.49x, and 0.09 m. Nothing changed but the size of the ball being hit.",
        seed=17004,
        overrides={"physics": {"ball_b_radius": 0.070, "launch_speed": 1.31}},
    ),
    RenderCase(
        case_id="table_marble_soft_push",
        description="The distractor: the hero's 100 mm and 50 mm pair, but a gentle 1.08 m/s "
        "push. The mass ratio is untouched, so the struck marble still leaves at 1.56x -- but "
        "1.56x of 0.35 m/s, and it only runs 0.12 m. That is near enough `heavier_target`'s "
        "0.09 m from the opposite cause, and the resting positions do not tell them apart. The "
        "big marble does: here it carries on 0.054 m forward, there it retreats 0.075 m.",
        seed=17005,
        overrides={"physics": {"ball_b_radius": 0.025, "launch_speed": 1.08}},
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a PCVE synthetic big/small marble collision benchmark suite.",
    )
    parser.add_argument(
        "--out-root", type=Path,
        default=WORKSPACE_DIR / "renders" / "pcve_table_marble_collision_suite",
    )
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--resolution", nargs=2, type=int, default=(1280, 720))
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument(
        "--duration-sec", type=float, default=2.4,
        help="Clip length. Long enough for the hero, which is the slowest to "
        "settle: its struck marble stops on frame 40 of 58.",
    )
    parser.add_argument("--samples", type=int, default=96)
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


def render_command(args, case: RenderCase, *, case_dir: Path,
                   overrides_path: Path) -> list[str]:
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
    video_source = case_dir / "table_marble_collision.mp4"
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
    """Pull the physics blocks back out of the rendered ground truth.

    The suite exists to compare *outcomes*, so the manifest records what each
    case actually did -- what the mass ratio worked out to, how much speed the
    struck marble left with against what the closed form says, which way the big
    one went, how far each of them ran -- rather than only the radius it was
    asked for.
    """
    gt = json.loads((case_dir / "ground_truth_transforms.json").read_text(encoding="utf-8"))
    physics = gt.get("physics", {})
    return {"collision": physics.get("collision", {}),
            "quality": physics.get("quality", {})}


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
    raise subprocess.CalledProcessError(result.returncode, command,
                                        result.stdout, result.stderr)


def main() -> None:
    args = parse_args()
    args.out_root.mkdir(parents=True, exist_ok=True)

    manifest_path = args.out_root / "suite_manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "suite_name": "pcve_table_marble_collision_suite",
        "description": "The same glass marble rolled along the same bar table with the same push "
        "into a second marble, varying only how big that second marble is -- and therefore, since "
        "both are the same glass, how heavy it is, by the cube of the size ratio. The four size "
        "cases run the whole range of a two-body impact, from the struck ball leaving half again "
        "as fast as the approach to the rolling ball being sent back the way it came. The "
        "soft-push case reproduces one of those outcomes without changing the masses at all.",
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

        command = render_command(args, case, case_dir=case_dir,
                                 overrides_path=overrides_path)
        record: dict[str, Any] = {
            "case_id": case.case_id,
            "description": case.description,
            "seed": int(case.seed),
            "duration_sec": (case.duration_sec if case.duration_sec is not None
                             else float(args.duration_sec)),
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
