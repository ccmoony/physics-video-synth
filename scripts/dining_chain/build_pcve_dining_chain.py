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
RENDER_SCRIPT = WORKSPACE_DIR / "scripts" / "dining_chain" / "render_dining_chain.py"


@dataclass(frozen=True)
class RenderCase:
    case_id: str
    description: str
    launch_speed: float
    table_friction: float


# The suite shows how far the sliding chain (can -> cup -> milk carton)
# propagates from the same starting layout, gated by the two knobs that
# control it: the strength of the push and the tabletop friction (a grippy
# finished-wood top vs a cloth). Everything else -- the objects, their 0.50 m
# spacing, the mostly-inelastic restitution -- is identical between cases.
# Every outcome below is verified against simulate_dining_chain.py.
CASES = (
    RenderCase(
        case_id="dining_chain_full",
        description="The full chain: a firm push (3.3 m/s) on the grippy finished-wood top "
        "carries the momentum can -> cup -> milk carton, and all three slide before coming to "
        "rest around frame 36.",
        launch_speed=3.3,
        table_friction=0.30,
    ),
    RenderCase(
        case_id="dining_chain_stops_at_cup",
        description="A softer push (1.8 m/s) on the same top: the can reaches the cup and shoves "
        "it, but the cup stalls before the milk carton -- the chain dies one link short for lack "
        "of speed, and the carton never moves.",
        launch_speed=1.8,
        table_friction=0.30,
    ),
    RenderCase(
        case_id="dining_chain_cloth_stops",
        description="The same firm 3.3 m/s push, but on a high-friction cloth (0.90): the can "
        "still reaches the cup, yet the cloth damps every slide so the cup can't reach the "
        "carton -- the chain dies short for friction rather than speed.",
        launch_speed=3.3,
        table_friction=0.90,
    ),
    RenderCase(
        case_id="dining_chain_no_chain",
        description="Too gentle a push (0.8 m/s): the can slides a little and stops before it "
        "even reaches the cup, so no collision happens at all.",
        launch_speed=0.8,
        table_friction=0.30,
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the dining-chain PCVE suite.")
    parser.add_argument("--out-root", type=Path, default=WORKSPACE_DIR / "renders" / "pcve_dining_chain_suite")
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--resolution", nargs=2, type=int, default=(1280, 720))
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--duration-sec", type=float, default=3.0)
    parser.add_argument("--samples", type=int, default=64)
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
        "--launch-speed", str(float(case.launch_speed)),
        "--table-friction", str(float(case.table_friction)),
    ]


def standardize_outputs(case_dir: Path) -> dict[str, str]:
    src = case_dir / "dining_chain.mp4"
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
        "suite": "dining_chain",
        "resolution": [int(args.resolution[0]), int(args.resolution[1])],
        "fps": int(args.fps),
        "duration_sec": float(args.duration_sec),
        "cases": [],
    }

    for case in CASES:
        case_dir = args.out_root / "cases" / case.case_id
        record: dict[str, Any] = {
            "case_id": case.case_id,
            "description": case.description,
            "launch_speed": case.launch_speed,
            "table_friction": case.table_friction,
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
        print(f"[suite] render {case.case_id} (friction={case.table_friction}, push={case.launch_speed})")
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
