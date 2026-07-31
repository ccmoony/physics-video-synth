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
RENDER_SCRIPT = (WORKSPACE_DIR / "scripts" / "table_drop_collision"
                 / "render_table_drop_collision.py")


@dataclass(frozen=True)
class RenderCase:
    case_id: str
    description: str
    seed: int
    overrides: dict[str, Any]
    duration_sec: float | None = None


# The scene has two independent halves and the suite is built to separate them.
#
# The first half is settled before the target is reached at all: once the ball
# leaves the table's west lip nothing touches it, so its landing point is fixed
# by the lip speed alone. The four cases below hold the push at 1.28 m/s, which
# means the ball leaves the lip at 1.053 m/s and lands at (-0.672, -0.290) in
# *every one of them* -- verified, the number does not move by a millimetre.
#
# The second half is what the suite varies, and it varies exactly one quantity:
# **the impact parameter**. Two spheres exchange momentum only along the line
# joining their centres, so sliding the target along y rotates that line and
# nothing else. Measured as the angle between the ball's approach and the line of
# centres, the four cases run 43.1 deg, 56.3 deg, 75.2 deg and then no contact at
# all -- an impact parameter of 0.683, 0.832, 0.967 and finally past 1. The
# struck ball's heading swings 180.0 -> 152.6 -> 142.5 deg and its travel
# collapses 0.304 -> 0.236 -> 0.078 m. The falling ball's own landing point never
# moves. That is the whole point: the outcome is decided by geometry the camera
# can see, not by anything hidden in the push.
#
# **The hero is as square as this collision gets, and it is still 43 deg off.**
# The ball is falling when it arrives, 68 deg below horizontal, while the line of
# centres is only 25 deg below horizontal. Nothing in the suite can close that
# gap: doing so means putting the target under the falling ball, and then the
# momentum goes into the floor and the target moves 14 mm. The suite therefore
# sweeps obliquity upward from its floor rather than through zero.
#
# The fifth case is the distractor, and it is why the suite is worth rendering.
# It restores the hero's target, dead in line, and softens the push to 1.22 m/s
# instead. That also produces a miss -- but for the opposite reason: the target
# is still exactly in line, and the ball simply lands 73 mm short of it. At rest
# the two misses look alike, both leaving the target untouched at its start
# position with the falling ball run out to the west. Telling them apart means
# watching the roll across the table, because the resting positions do not.
#
# Every number here was measured by running simulate_table_drop_collision.py,
# not chosen. The outcomes are emergent; the cases only set inputs.
#
# Note the durations. The default 2.8 s is enough for the two cases that end in a
# solid collision, because both balls are stopped by frame 39. The graze and the
# two misses leave the falling ball running across the floor and it does not come
# to rest until frame 72-77, so those three ask for 3.4 s. Rendering them at
# 2.8 s is not wrong, it just ends the clip on a ball that is still moving.
CASES = (
    RenderCase(
        case_id="table_drop_collision_in_line",
        description="The hero, and the squarest hit the scene can produce. The target sits dead "
        "in line with the lane at y = -0.290, so the whole exchange stays in one vertical plane: "
        "the contact normal has no sideways component, the target is driven due west at "
        "1.297 m/s and runs 0.304 m, and the ball that hit it is thrown due east, exactly 180.0 "
        "deg away, running 0.736 m. Both finish on y = -0.290, the line they started on. It is "
        "still 43.1 deg off the line of centres -- an impact parameter of 0.683 -- because the "
        "ball arrives falling, and that is the floor this suite sweeps up from.",
        seed=11001,
        overrides={"physics": {"ball_b_y": -0.290}},
    ),
    RenderCase(
        case_id="table_drop_collision_offset",
        description="The target slid 32 mm north, to y = -0.258. The line of centres swings out "
        "of the fall plane and the impact parameter rises to 0.832: the target now leaves on "
        "152.6 deg rather than due west, at 1.152 m/s, and runs 0.236 m. The ball that hit it is "
        "thrown back and toward the camera instead of straight east, and runs 0.965 m. Same "
        "landing point, same push -- only the line joining the two centres has moved.",
        seed=11002,
        overrides={"physics": {"ball_b_y": -0.258}},
    ),
    RenderCase(
        case_id="table_drop_collision_graze",
        description="12 mm further, to y = -0.246, and the falling ball only clips the crown. The "
        "impact parameter is 0.967, within a thirtieth of a complete miss, and almost nothing is "
        "transferred: the target leaves at 0.673 m/s on 142.5 deg and stops after 0.078 m, while "
        "the ball that hit it barely notices and carries on for 1.013 m.",
        seed=11003,
        overrides={"physics": {"ball_b_y": -0.246}},
        duration_sec=3.4,
    ),
    RenderCase(
        case_id="table_drop_collision_out_of_line",
        description="11 mm further again, to y = -0.235, and the two never touch. The falling "
        "ball lands exactly where it lands in every other case, at (-0.672, -0.290), and passes "
        "north of the target with no contact registered at all; it then runs 1.177 m west across "
        "the floor. The target does not move.",
        seed=11004,
        overrides={"physics": {"ball_b_y": -0.235}},
        duration_sec=3.4,
    ),
    RenderCase(
        case_id="table_drop_collision_soft_push",
        description="The distractor. The hero's target, still dead in line at y = -0.290, but the "
        "push is softened from 1.28 to 1.22 m/s. The ball reaches the lip at 0.980 m/s instead of "
        "1.053, is thrown 23 mm less far, and lands at (-0.649, -0.290) -- 73 mm short of the "
        "target, which it therefore misses. The end state is the one out_of_line reaches: target "
        "untouched at its start position, falling ball run out west. Only the speed across the "
        "table and the shorter throw off the lip say which mechanism produced it.",
        seed=11005,
        overrides={"physics": {"launch_speed": 1.22, "ball_b_y": -0.290}},
        duration_sec=3.4,
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a PCVE synthetic tennis-ball-off-a-table benchmark suite.",
    )
    parser.add_argument(
        "--out-root", type=Path,
        default=WORKSPACE_DIR / "renders" / "pcve_table_drop_collision_suite",
    )
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--resolution", nargs=2, type=int, default=(1280, 720))
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument(
        "--duration-sec", type=float, default=2.8,
        help="Default clip length, enough for the cases that end in a collision. The graze and "
        "the two misses leave the falling ball running and override this with 3.4 s.",
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
    video_source = case_dir / "table_drop_collision.mp4"
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
    case actually did -- whether the two balls met, on which frame, where each
    came to rest and how far it ran -- rather than only the inputs asked for.
    """
    gt = json.loads((case_dir / "ground_truth_transforms.json").read_text(encoding="utf-8"))
    return gt.get("physics", {}).get("quality", {})


def landing_point(case_dir: Path) -> list[float] | None:
    """The falling ball's predicted touchdown, recorded per case on purpose.

    This is the suite's standing check on its own premise: the four geometry
    cases vary only where the target sits, so the ball's own landing point has to
    come out identical in all of them. If it ever does not, something has leaked
    from the target's position back into the launch and the cases are no longer
    comparing what they claim to compare.
    """
    gt = json.loads((case_dir / "ground_truth_transforms.json").read_text(encoding="utf-8"))
    flight = gt.get("physics", {}).get("launch", {}).get("predicted_flight") or {}
    return flight.get("landing")


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
        "suite_name": "pcve_table_drop_collision_suite",
        "description": "The same tennis ball rolled off the same coffee table at the same speed, "
        "onto a second tennis ball waiting on the floor, varying only the impact parameter -- "
        "where that target sits across the line of flight. Covers a square in-line strike that "
        "sends the two balls 180 deg apart, an offset one that bends both paths out of the fall "
        "plane, a graze that barely moves the target, a clean miss, and -- as a distractor -- a "
        "miss produced instead by a softer push.",
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
            record["landing"] = landing_point(case_dir)
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
        record["landing"] = landing_point(case_dir)
        record["elapsed_sec"] = round(time.perf_counter() - start, 3)
        record["status"] = "completed"
        write_json(manifest_path, manifest)
        print(f"[suite] completed {case.case_id} in {record['elapsed_sec']:.1f}s")

    report_shared_landing(manifest)
    print(f"[suite] manifest={manifest_path.resolve()}")


def report_shared_landing(manifest: dict[str, Any]) -> None:
    """Say out loud whether the geometry cases really did share a landing point.

    Only the four cases that leave the push alone are compared; the distractor
    changes the push on purpose and is expected to land somewhere else.
    """
    landings = {
        case["case_id"]: case.get("landing")
        for case in manifest["cases"]
        if case.get("landing") and case["case_id"] != "table_drop_collision_soft_push"
    }
    if len(landings) < 2:
        return
    first = next(iter(landings.values()))
    spread = max(
        max(abs(p[0] - first[0]), abs(p[1] - first[1])) for p in landings.values()
    )
    if spread <= 1e-4:
        print(f"[suite] all {len(landings)} fixed-push cases land at "
              f"({first[0]:.3f}, {first[1]:.3f}); only the target moved.")
    else:
        print(f"[WARN] the fixed-push cases do not share a landing point; they "
              f"spread by {spread * 1000:.1f} mm. Something has leaked from the "
              "target's position into the launch and the cases are no longer "
              "varying only the geometry.")


if __name__ == "__main__":
    main()
