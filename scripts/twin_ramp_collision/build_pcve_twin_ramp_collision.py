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
RENDER_SCRIPT = WORKSPACE_DIR / "scripts" / "twin_ramp_collision" / "render_twin_ramp_collision.py"


@dataclass(frozen=True)
class RenderCase:
    case_id: str
    description: str
    seed: int
    overrides: dict[str, Any]
    duration_sec: float | None = None


# The apparatus is identical in every case: the same two ramps, the same two
# marbles released together from the same crests. Only what happens *at the
# impact* changes, which makes this a clean collision benchmark -- the approach
# is a controlled constant across the first four cases, arriving at 0.92 m/s
# every time, so any difference on screen after 1.42 s is the collision itself.
#
# The axis being varied is worth stating, because it is not the obvious one.
# These marbles arrive rolling, so each carries angular momentum that a head-on
# impulse does not touch: a ball rebounds translating backwards while still
# spinning forwards, and track friction then has to reverse that spin, eating
# most of the rebound. The settled rebound works out to (2.5e - 1)/3.5 of the
# approach speed for a solid sphere, so it collapses to nothing around e = 0.4 --
# far higher than the restitution at which you would naively expect it to.
#
# The fifth case is the distractor and is why the suite is worth rendering. It
# leaves the collision alone and slows the *approach* instead, via a dusty
# track. It ends up looking much like a soft-ball collision -- two marbles that
# meet and barely separate -- for an entirely unrelated reason. The resting
# positions do not tell them apart; only how fast the balls were going, and how
# long they took to get there, does.
#
# Every number quoted below is measured from simulate_twin_ramp_collision.py,
# not intended. The two balls are referred to by colour: ball A is the blue one
# at +x, ball B the amber one at -x.
CASES = (
    RenderCase(
        case_id="twin_ramp_collision_matched",
        description="The hero case. Two matched glass marbles released together, meeting head-on "
        "1.42 s in at x = +2 mm -- the middle of the valley -- doing 0.920 and 0.916 m/s. They "
        "separate at only 0.25 m/s, close to a quarter of what they arrived with, because the "
        "impact leaves their spin untouched and friction has to unwind it. Each runs 142 mm back "
        "up its ramp, rolls down again and comes to rest 82 mm apart in the valley.",
        seed=11001,
        overrides={"physics": {"ball_restitution": 0.87}},
    ),
    RenderCase(
        case_id="twin_ramp_collision_soft_balls",
        description="Identical approach -- same 0.92 m/s, same 1.42 s, same spot -- but soft "
        "balls (restitution 0.40, so e = 0.16 ball-on-ball). They come apart at 0.11 m/s, barely "
        "move, and ball A gets only 41 mm back up its ramp against the hero case's 142 mm before "
        "rolling straight back down. They finish resting against each other rather than apart.",
        seed=11002,
        overrides={"physics": {"ball_restitution": 0.40}},
    ),
    RenderCase(
        case_id="twin_ramp_collision_lively",
        description="Identical approach, hard balls (restitution 0.97). They come off at 0.39 "
        "m/s and run 192 mm back up their ramps, clearly further than the hero case -- but still "
        "nowhere near the 0.9 m/s a spin-free elastic collision would give. Even at the top of "
        "the restitution range the rolling penalty dominates.",
        seed=11003,
        overrides={"physics": {"ball_restitution": 0.97}},
    ),
    RenderCase(
        case_id="twin_ramp_collision_uneven_release",
        description="The only case where the two sides differ. Ball B starts 250 mm further down "
        "its ramp, so it reaches the valley at 0.64 m/s against ball A's 0.86. They meet early, "
        "at 1.29 s, and 108 mm off centre on A's side. A is almost stopped by the exchange (0.06 "
        "m/s) while B is thrown back at 0.45, and A is then carried past the middle onto B's half "
        "of the track.",
        seed=11004,
        overrides={"physics": {"release_inset_bias": 0.25}},
    ),
    RenderCase(
        case_id="twin_ramp_collision_dusty_track",
        description="The distractor: an untouched collision on a dusty track (rolling friction "
        "0.005 instead of 0.0012). The marbles lose most of the descent to the surface and creep "
        "into the valley at 0.54 m/s, meeting 0.7 s late at 2.12 s and separating at 0.17 m/s. "
        "The gentle meeting and the near-touching rest positions look like a soft-ball "
        "collision; the approach speed and the timing are the only things that say otherwise.",
        seed=11005,
        overrides={"physics": {"ball_rolling_friction": 0.005}},
        duration_sec=3.6,
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a PCVE synthetic twin-ramp collision benchmark suite.",
    )
    parser.add_argument(
        "--out-root", type=Path,
        default=WORKSPACE_DIR / "renders" / "pcve_twin_ramp_collision_suite",
    )
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--resolution", nargs=2, type=int, default=(1280, 720))
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument(
        "--duration-sec", type=float, default=3.0,
        help="Default clip length. The dusty-track case needs longer to resolve and "
        "overrides this with its own value.",
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
    video_source = case_dir / "twin_ramp_collision.mp4"
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

    The suite exists to compare outcomes, so the manifest records what each case
    actually did -- when and where the balls met, how fast they were going, and
    how fast they left -- rather than only the inputs it was asked for.
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
        "suite_name": "pcve_twin_ramp_collision_suite",
        "description": "The same two steel balls released together from the same pair of ramps, "
        "varying only what happens when they meet: a matched exchange, soft balls that barely "
        "separate, a livelier rebound, an uneven release that shifts the meeting point off "
        "centre, and a dusty track that slows the approach instead of changing the collision.",
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
            "duration_sec": (
                case.duration_sec if case.duration_sec is not None else float(args.duration_sec)
            ),
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

        record["status"] = "ok"
        record["elapsed_sec"] = round(time.perf_counter() - start, 3)
        record["outputs"] = standardize_render_outputs(case_dir)
        record["outcome"] = case_outcome(case_dir)
        write_json(manifest_path, manifest)

    print(f"[suite] manifest written to {manifest_path}")


if __name__ == "__main__":
    main()
