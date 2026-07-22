from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pybullet as p


# A three-object sliding chain on a dining tabletop: a cola can is given a
# horizontal push, slides into a soda cup, and the cup in turn slides into a
# milk carton. Everything is emergent from the can's single initial velocity
# (no per-object impulse), exactly like the domino_chain / car_ramp_climb
# scenes. The objects are low, flat-based drink containers so they *slide*
# rather than roll or topple; the tabletop friction is low enough that each
# hit passes momentum to the next but high enough that nobody slides off the
# far edge. Coordinates are the real modern dining-room world frame
# (dining_room__kichen_baked.glb): tabletop surface at z=0.778, the chain laid
# out along +Y at x=1.15 (a clear stretch clear of the table's centre vase).
TABLE_TOP_Z = 0.778
CHAIN_X = 1.15

# Collision proxies (metres) -- simple cylinders/box matching each container's
# real-world size; the detailed GLB meshes are visual-only at render time.
# Gaps of 0.50 m plus a firm push keep the realistic (grippy) tabletop friction
# but spread the action out in distance instead: the can slides in and the
# chain runs can->cup->milk with everything coming to rest around frame 36
# (~1.5 s), rather than being over by frame 25 with tighter spacing.
CAN = {"kind": "cyl", "r": 0.034, "h": 0.122, "mass": 0.36, "start_y": -1.20}
CUP = {"kind": "cyl", "r": 0.044, "h": 0.160, "mass": 0.30, "start_y": -0.70}
# Small single-serve carton, sized close to the can (~12.5 cm tall) rather than
# a full 20 cm carton, so it doesn't tower over the other two.
MILK = {"kind": "box", "hx": 0.027, "hy": 0.028, "hz": 0.0625, "mass": 0.35, "start_y": -0.20}

ORDER = ("can", "cup", "milk")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--duration-sec", type=float, default=3.0)
    parser.add_argument("--substeps", type=int, default=40)
    parser.add_argument(
        "--launch-speed", type=float, default=3.3,
        help="Initial +Y push given to the can (m/s). Firm enough that, across "
        "the wide 0.50 m gaps on the grippy top, the chain reaches the carton "
        "and comes to rest around frame 36 without the carton sliding off.",
    )
    parser.add_argument(
        "--table-friction", type=float, default=0.30,
        help="Tabletop lateral friction -- the scene's main knob. PyBullet "
        "combines it with the object friction (0.30) for an effective ~0.09: a "
        "realistic finished-wood top where the objects clearly decelerate as "
        "they slide. Higher (a cloth/placemat) damps each slide so the chain "
        "dies short.",
    )
    parser.add_argument("--object-friction", type=float, default=0.30)
    parser.add_argument("--restitution", type=float, default=0.1)
    parser.add_argument("--gravity-z", type=float, default=-9.8)
    return parser.parse_args()


def up_axis_z(quat_xyzw) -> float:
    x, y, z, w = quat_xyzw
    return 1.0 - 2.0 * (x * x + y * y)


def simulate(args: argparse.Namespace) -> dict:
    fps = int(args.fps)
    frame_end = max(2, int(round(float(args.duration_sec) * fps)))
    substeps = int(args.substeps)
    dt = 1.0 / float(fps * substeps)

    client = p.connect(p.DIRECT)
    try:
        p.resetSimulation(physicsClientId=client)
        p.setGravity(0.0, 0.0, float(args.gravity_z), physicsClientId=client)
        p.setTimeStep(dt, physicsClientId=client)
        p.setPhysicsEngineParameter(
            fixedTimeStep=dt, numSolverIterations=200,
            deterministicOverlappingPairs=1, enableConeFriction=1,
            physicsClientId=client,
        )

        table_shape = p.createCollisionShape(
            p.GEOM_BOX, halfExtents=(4.0, 4.0, 0.4), physicsClientId=client,
        )
        table_id = p.createMultiBody(
            0.0, table_shape, -1, (0.0, 0.0, TABLE_TOP_Z - 0.4), physicsClientId=client,
        )
        p.changeDynamics(
            table_id, -1, lateralFriction=float(args.table_friction),
            restitution=float(args.restitution), physicsClientId=client,
        )

        def make_cyl(spec):
            shape = p.createCollisionShape(
                p.GEOM_CYLINDER, radius=spec["r"], height=spec["h"], physicsClientId=client,
            )
            z = TABLE_TOP_Z + spec["h"] / 2.0 + 0.001
            return p.createMultiBody(
                spec["mass"], shape, -1, (CHAIN_X, spec["start_y"], z), physicsClientId=client,
            ), z

        def make_box(spec):
            shape = p.createCollisionShape(
                p.GEOM_BOX, halfExtents=(spec["hx"], spec["hy"], spec["hz"]),
                physicsClientId=client,
            )
            z = TABLE_TOP_Z + spec["hz"] + 0.001
            return p.createMultiBody(
                spec["mass"], shape, -1, (CHAIN_X, spec["start_y"], z), physicsClientId=client,
            ), z

        can_id, can_z = make_cyl(CAN)
        cup_id, cup_z = make_cyl(CUP)
        milk_id, milk_z = make_box(MILK)

        bodies = {"can": can_id, "cup": cup_id, "milk": milk_id}
        start_z = {"can": can_z, "cup": cup_z, "milk": milk_z}
        for name, body in bodies.items():
            spec = {"can": CAN, "cup": CUP, "milk": MILK}[name]
            p.changeDynamics(
                body, -1, lateralFriction=float(args.object_friction),
                spinningFriction=0.02, rollingFriction=0.002,
                restitution=float(args.restitution), physicsClientId=client,
            )
        p.resetBaseVelocity(
            can_id, linearVelocity=(0.0, float(args.launch_speed), 0.0),
            angularVelocity=(0.0, 0.0, 0.0), physicsClientId=client,
        )

        start_y = {name: {"can": CAN, "cup": CUP, "milk": MILK}[name]["start_y"] for name in ORDER}
        frames = []
        for frame_index in range(1, frame_end + 1):
            if frame_index > 1:
                for _ in range(substeps):
                    p.stepSimulation(physicsClientId=client)
            entry = {"frame_index": frame_index, "time_sec": (frame_index - 1) / float(fps), "objects": {}}
            for name in ORDER:
                pos, quat = p.getBasePositionAndOrientation(bodies[name], physicsClientId=client)
                lin, ang = p.getBaseVelocity(bodies[name], physicsClientId=client)
                entry["objects"][name] = {
                    "location": list(pos),
                    "quaternion_xyzw": list(quat),
                    "linear_velocity": list(lin),
                    "angular_velocity": list(ang),
                }
            frames.append(entry)

        quality = {}
        for name in ORDER:
            first = frames[0]["objects"][name]
            last = frames[-1]["objects"][name]
            quality[name] = {
                "displacement_y": last["location"][1] - start_y[name],
                "final_y": last["location"][1],
                "final_up_z": up_axis_z(last["quaternion_xyzw"]),
                "final_speed": math.hypot(last["linear_velocity"][0], last["linear_velocity"][1]),
            }
        chain_ok = quality["cup"]["displacement_y"] > 0.03 and quality["milk"]["displacement_y"] > 0.03

        return {
            "schema_version": 1,
            "simulator": "pybullet",
            "fps": fps,
            "frame_start": 1,
            "frame_end": frame_end,
            "duration_sec": float(args.duration_sec),
            "substeps_per_frame": substeps,
            "physics_dt": dt,
            "table_top_z": TABLE_TOP_Z,
            "chain_x": CHAIN_X,
            "objects_geometry": {"can": CAN, "cup": CUP, "milk": MILK},
            "object_order": list(ORDER),
            "params": {
                "launch_speed": float(args.launch_speed),
                "table_friction": float(args.table_friction),
                "object_friction": float(args.object_friction),
                "restitution": float(args.restitution),
            },
            "quality": {**quality, "chain_ok": chain_ok, "start_z": start_z, "start_y": start_y},
            "frames": frames,
        }
    finally:
        p.disconnect(client)


def main() -> None:
    args = parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    records = simulate(args)
    args.out.write_text(json.dumps(records, indent=2), encoding="utf-8")
    q = records["quality"]
    print(f"[sim] chain_ok={q['chain_ok']}  "
          f"can dY={q['can']['displacement_y']:+.2f}  "
          f"cup dY={q['cup']['displacement_y']:+.2f}  "
          f"milk dY={q['milk']['displacement_y']:+.2f} (up_z {q['milk']['final_up_z']:+.2f})")


if __name__ == "__main__":
    main()
