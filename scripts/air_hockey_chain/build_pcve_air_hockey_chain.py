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
RENDER_SCRIPT = (
    WORKSPACE_DIR / "scripts" / "air_hockey_chain" / "render_air_hockey_chain.py"
)


@dataclass(frozen=True)
class RenderCase:
    case_id: str
    description: str
    push_speed: float
    mallet_restitution: float
    surface_friction: float
    middle_mass_scale: float


# The relay in this scene rests on three properties of a real air-hockey table,
# and the suite breaks them one at a time from an identical starting layout --
# three identical mallets on the quarter, half and three-quarter points of the
# table, one push on the far one. What is being tested is whether a model has
# actually learned the equal-mass near-elastic collision or is just pattern
# matching "disc moves, next disc moves".
#
#   equal masses      -> middle_mass_scale
#   hard faces        -> mallet_restitution (PyBullet multiplies the pair, so
#                        mallet-on-mallet is this squared)
#   the air cushion   -> surface_friction (likewise multiplied against the
#                        mallet's own friction, so the effective coefficient is
#                        this squared)
#
# Every outcome below was verified against simulate_air_hockey_chain.py at the
# suite's own defaults, and each one matches the closed-form prediction for a
# 1-D collision, v1' = v1(m1 - e*m2)/(m1 + m2) and v2' = v1*m1(1 + e)/(m1 + m2).
CASES = (
    RenderCase(
        case_id="air_hockey_chain_baseline",
        description="The relay as intended: equal masses and near-elastic faces (pair "
        "restitution 0.90) mean each striker hands over essentially all of its speed and "
        "stops dead where it hit. Blue keeps 3% of its speed, red 4% -- against the "
        "(1-e)/2 = 4.9% the pair restitution predicts -- and white carries 88% of the "
        "original push into the near rail.",
        push_speed=0.8,
        mallet_restitution=0.95,
        surface_friction=0.06,
        middle_mass_scale=1.0,
    ),
    RenderCase(
        case_id="air_hockey_chain_heavy_middle",
        description="The middle mallet is four times the mass of the other two. Momentum is "
        "no longer cancelled at the impact: blue rebounds backwards up the table at half its "
        "incoming speed instead of stopping, and red leaves with only 0.30 m/s. The chain "
        "still reaches white, but at 31% of the push rather than 88%.",
        push_speed=0.8,
        mallet_restitution=0.95,
        surface_friction=0.06,
        middle_mass_scale=4.0,
    ),
    RenderCase(
        case_id="air_hockey_chain_dead_faces",
        description="Soft mallet faces (restitution 0.35, so 0.12 for the pair) turn each "
        "impact into a shove. The striker keeps about 41% of its speed instead of a few "
        "percent, so it does not stop -- it slides on behind the disc it just hit, and all "
        "three finish bunched together near the near rail rather than strung out along the "
        "table where each handoff happened.",
        push_speed=0.8,
        mallet_restitution=0.35,
        surface_friction=0.06,
        middle_mass_scale=1.0,
    ),
    RenderCase(
        case_id="air_hockey_chain_no_air_cushion",
        description="The air cushion switched off: surface friction 0.5 instead of 0.06, so "
        "the effective coefficient is 0.25 rather than a few thousandths. Blue decelerates "
        "at mu*g and stops after 13 cm, well short of red. No collision happens at all -- "
        "the table being nearly frictionless is what makes the relay possible in the first "
        "place, not a detail of it.",
        push_speed=0.8,
        mallet_restitution=0.95,
        surface_friction=0.5,
        middle_mass_scale=1.0,
    ),
    RenderCase(
        case_id="air_hockey_chain_soft_push",
        description="Everything correct but the push is too gentle (0.3 m/s). Blue still "
        "reaches red and still stops dead on it -- the collision physics is untouched -- but "
        "red leaves with only 0.22 m/s and runs out of table before it reaches white. The "
        "chain dies one link short, and white never moves.",
        push_speed=0.3,
        mallet_restitution=0.95,
        surface_friction=0.06,
        middle_mass_scale=1.0,
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the air-hockey mallet relay PCVE suite."
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=WORKSPACE_DIR / "renders" / "pcve_air_hockey_chain_suite",
    )
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--resolution", nargs=2, type=int, default=(1280, 720))
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--duration-sec", type=float, default=3.0)
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--device", choices=("auto", "cpu"), default="auto")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def render_command(
    args: argparse.Namespace, case: RenderCase, case_dir: Path
) -> list[str]:
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
        "--push-speed", str(float(case.push_speed)),
        "--mallet-restitution", str(float(case.mallet_restitution)),
        "--surface-friction", str(float(case.surface_friction)),
        "--middle-mass-scale", str(float(case.middle_mass_scale)),
    ]


def standardize_outputs(case_dir: Path) -> dict[str, str]:
    src = case_dir / "air_hockey_chain.mp4"
    if not src.exists():
        matches = sorted(case_dir.glob("*.mp4"))
        if not matches:
            raise FileNotFoundError(f"No mp4 found in {case_dir}")
        src = matches[0]
    dst = case_dir / "video.mp4"
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    return {"video": str(dst.resolve())}


def main() -> None:
    args = parse_args()
    args.out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out_root / "pcve_manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "suite": "air_hockey_chain",
        "description": "Three identical air-hockey mallets in a line; the far one is given a "
        "single push. Cases break, one at a time, the three properties the relay rests on: "
        "equal masses, near-elastic faces, and the near-frictionless air cushion.",
        "resolution": [int(args.resolution[0]), int(args.resolution[1])],
        "fps": int(args.fps),
        "duration_sec": float(args.duration_sec),
        "samples": int(args.samples),
        "cases": [],
    }

    for case in CASES:
        case_dir = args.out_root / "cases" / case.case_id
        record: dict[str, Any] = {
            "case_id": case.case_id,
            "description": case.description,
            "push_speed": case.push_speed,
            "mallet_restitution": case.mallet_restitution,
            "surface_friction": case.surface_friction,
            "middle_mass_scale": case.middle_mass_scale,
            "case_dir": str(case_dir.resolve()),
        }
        manifest["cases"].append(record)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        expected = case_dir / "video.mp4"
        if args.skip_existing and expected.exists():
            record["status"] = "skipped_existing"
            record["outputs"] = {"video": str(expected.resolve())}
            print(f"[suite] skip existing {case.case_id}")
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            continue

        command = render_command(args, case, case_dir)
        if args.dry_run:
            record["status"] = "dry_run"
            record["command"] = command
            print(" ".join(command))
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            continue

        case_dir.mkdir(parents=True, exist_ok=True)
        print(
            f"[suite] render {case.case_id} (push={case.push_speed}, "
            f"restitution={case.mallet_restitution}, friction={case.surface_friction}, "
            f"middle_mass={case.middle_mass_scale})"
        )
        started = time.time()
        subprocess.run(command, check=True)
        record["elapsed_sec"] = round(time.time() - started, 1)
        record["outputs"] = standardize_outputs(case_dir)
        record["status"] = "completed"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"[suite] completed {case.case_id} in {record['elapsed_sec']:.1f}s")

    print(f"[suite] manifest={manifest_path.resolve()}")


if __name__ == "__main__":
    main()
