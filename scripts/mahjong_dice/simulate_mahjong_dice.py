from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pybullet as p


# Scene geometry matches render_mahjong_dice.py so the PyBullet trajectory
# can be applied directly as Blender keyframes.  Dimensions are taken from
# assets/models/riichi_mahjong.glb: the two decorative dice sitting in the
# table's center tray are perfect 0.0833-unit cubes resting at world Z
# 0.9382 (both at the exact same height, confirming a flat tray surface).
DIE_EDGE = 0.0833
TRAY_Z = 0.9382
FLOOR_Z = TRAY_Z - DIE_EDGE / 2.0

# Original decorative resting spots (die center), used as the drop targets.
DIE_0_XY = (-0.1612, -0.0052)
DIE_1_XY = (-0.1991, 0.1425)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--duration-sec", type=float, default=3.0)
    parser.add_argument("--substeps", type=int, default=12)
    parser.add_argument("--die-edge", type=float, default=DIE_EDGE)
    parser.add_argument("--die-mass", type=float, default=0.006)
    parser.add_argument("--die-friction", type=float, default=0.5)
    parser.add_argument("--die-restitution", type=float, default=0.72)
    parser.add_argument("--floor-friction", type=float, default=0.55)
    parser.add_argument("--drop-height", type=float, default=0.6)
    parser.add_argument("--gravity-z", type=float, default=-9.8)
    parser.add_argument("--floor-z", type=float, default=FLOOR_Z)
    parser.add_argument(
        "--die-0-xy", nargs=2, type=float, default=list(DIE_0_XY),
    )
    parser.add_argument(
        "--die-1-xy", nargs=2, type=float, default=list(DIE_1_XY),
    )
    return parser.parse_args()


def simulate(args: argparse.Namespace) -> dict:
    fps = int(args.fps)
    frame_end = max(2, int(round(float(args.duration_sec) * fps)))
    substeps = int(args.substeps)
    dt = 1.0 / float(fps * substeps)

    edge = float(args.die_edge)
    half_edge = edge / 2.0
    floor_z = float(args.floor_z)
    drop_height = float(args.drop_height)

    die_xy = [tuple(args.die_0_xy), tuple(args.die_1_xy)]
    initial_locations = [
        (x, y, floor_z + half_edge + drop_height + 0.05 * idx)
        for idx, (x, y) in enumerate(die_xy)
    ]
    # No initial spin: the dice drop straight down, level, and only pick up
    # rotation from the bounce itself.
    initial_angular_velocities = [
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
    ]
    identity_quat = (0.0, 0.0, 0.0, 1.0)
    initial_orientations = [identity_quat, identity_quat]

    client = p.connect(p.DIRECT)
    try:
        p.resetSimulation(physicsClientId=client)
        p.setGravity(0.0, 0.0, float(args.gravity_z), physicsClientId=client)
        p.setTimeStep(dt, physicsClientId=client)
        p.setPhysicsEngineParameter(
            fixedTimeStep=dt,
            numSolverIterations=300,
            contactBreakingThreshold=0.0005,
            deterministicOverlappingPairs=1,
            enableConeFriction=1,
            physicsClientId=client,
        )

        floor_shape = p.createCollisionShape(
            p.GEOM_BOX,
            halfExtents=(1.2, 1.2, 0.05),
            physicsClientId=client,
        )
        floor_id = p.createMultiBody(
            0.0,
            floor_shape,
            -1,
            (die_xy[0][0], die_xy[0][1], floor_z - 0.05),
            physicsClientId=client,
        )
        p.changeDynamics(
            floor_id,
            -1,
            lateralFriction=float(args.floor_friction),
            restitution=float(args.die_restitution),
            physicsClientId=client,
        )

        die_shape = p.createCollisionShape(
            p.GEOM_BOX,
            halfExtents=(half_edge, half_edge, half_edge),
            physicsClientId=client,
        )
        die_ids = []
        for location, orientation, ang_vel in zip(
            initial_locations, initial_orientations, initial_angular_velocities
        ):
            die_id = p.createMultiBody(
                baseMass=float(args.die_mass),
                baseCollisionShapeIndex=die_shape,
                baseVisualShapeIndex=-1,
                basePosition=location,
                baseOrientation=orientation,
                physicsClientId=client,
            )
            p.resetBaseVelocity(
                die_id,
                linearVelocity=(0.0, 0.0, 0.0),
                angularVelocity=ang_vel,
                physicsClientId=client,
            )
            p.changeDynamics(
                die_id,
                -1,
                lateralFriction=float(args.die_friction),
                spinningFriction=0.01,
                rollingFriction=0.0008,
                restitution=float(args.die_restitution),
                linearDamping=0.02,
                angularDamping=0.05,
                physicsClientId=client,
            )
            die_ids.append(die_id)

        frames = []

        for frame_index in range(1, frame_end + 1):
            if frame_index > 1:
                for _ in range(substeps):
                    p.stepSimulation(physicsClientId=client)

            die_data = []
            for die_id in die_ids:
                dpos, dquat = p.getBasePositionAndOrientation(die_id, physicsClientId=client)
                dlin, dang = p.getBaseVelocity(die_id, physicsClientId=client)
                die_data.append({
                    "location": list(dpos),
                    "quaternion_xyzw": list(dquat),
                    "linear_velocity": list(dlin),
                    "angular_velocity": list(dang),
                })

            frames.append({
                "frame_index": frame_index,
                "time_sec": (frame_index - 1) / float(fps),
                "dice": die_data,
            })

        def up_face_axis(quat: tuple) -> str:
            rot = p.getMatrixFromQuaternion(quat)
            axes = {
                "+X": (rot[0], rot[3], rot[6]),
                "-X": (-rot[0], -rot[3], -rot[6]),
                "+Y": (rot[1], rot[4], rot[7]),
                "-Y": (-rot[1], -rot[4], -rot[7]),
                "+Z": (rot[2], rot[5], rot[8]),
                "-Z": (-rot[2], -rot[5], -rot[8]),
            }
            return max(axes, key=lambda k: axes[k][2])

        final_frame = frames[-1]["dice"]
        settled = []
        up_faces = []
        for die_data in final_frame:
            lin_speed = math.sqrt(sum(v * v for v in die_data["linear_velocity"]))
            ang_speed = math.sqrt(sum(v * v for v in die_data["angular_velocity"]))
            settled.append(bool(lin_speed < 0.02 and ang_speed < 0.05))
            up_faces.append(up_face_axis(tuple(die_data["quaternion_xyzw"])))

        return {
            "schema_version": 2,
            "simulator": "pybullet",
            "fps": fps,
            "frame_start": 1,
            "frame_end": frame_end,
            "duration_sec": float(args.duration_sec),
            "substeps_per_frame": substeps,
            "physics_dt": dt,
            "objects": {
                "dice": {
                    "edge": edge,
                    "count": len(die_ids),
                    "mass": float(args.die_mass),
                    "initial_locations": [list(loc) for loc in initial_locations],
                    "friction": float(args.die_friction),
                    "restitution": float(args.die_restitution),
                },
                "floor": {
                    "friction": float(args.floor_friction),
                    "z": floor_z,
                },
            },
            "quality": {
                "settled": settled,
                "up_face_local_axis": up_faces,
            },
            "frames": frames,
        }
    finally:
        p.disconnect(client)


def main() -> None:
    args = parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    records = simulate(args)
    args.out.write_text(json.dumps(records, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
