from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pybullet as p


# Scene geometry matches render_domino_chain.py so the PyBullet trajectory
# can be applied directly as Blender keyframes.  The domino dimensions are
# taken from assets/models/domino_test.glb (a single upright tile, local
# X = thickness/row axis, Y = width, Z = height).
FLOOR_Z = -0.0322
DOMINO_THICKNESS = 0.20
DOMINO_WIDTH = 0.70
DOMINO_HEIGHT = 1.30


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--duration-sec", type=float, default=3.0)
    parser.add_argument("--substeps", type=int, default=12)
    parser.add_argument("--domino-count", type=int, default=4)
    parser.add_argument("--domino-spacing", type=float, default=0.55)
    parser.add_argument("--domino-thickness", type=float, default=DOMINO_THICKNESS)
    parser.add_argument("--domino-width", type=float, default=DOMINO_WIDTH)
    parser.add_argument("--domino-height", type=float, default=DOMINO_HEIGHT)
    parser.add_argument("--domino-mass", type=float, default=0.12)
    parser.add_argument("--domino-friction", type=float, default=0.6)
    parser.add_argument("--domino-restitution", type=float, default=0.05)
    parser.add_argument("--floor-friction", type=float, default=0.6)
    parser.add_argument("--push-angle-deg", type=float, default=12.0)
    parser.add_argument("--gravity-z", type=float, default=-9.81)
    parser.add_argument("--floor-z", type=float, default=FLOOR_Z)
    parser.add_argument("--scene-offset-x", type=float, default=0.0)
    parser.add_argument("--scene-offset-y", type=float, default=0.0)
    return parser.parse_args()


def simulate(args: argparse.Namespace) -> dict:
    fps = int(args.fps)
    frame_end = max(2, int(round(float(args.duration_sec) * fps)))
    substeps = int(args.substeps)
    dt = 1.0 / float(fps * substeps)

    count = int(args.domino_count)
    if count < 2:
        raise ValueError("--domino-count must be at least 2 for a chain reaction.")
    spacing = float(args.domino_spacing)
    thickness = float(args.domino_thickness)
    width = float(args.domino_width)
    height = float(args.domino_height)
    floor_z = float(args.floor_z)
    offset_x = float(args.scene_offset_x)
    offset_y = float(args.scene_offset_y)
    half_height = height / 2.0

    row_start = -(count - 1) * spacing / 2.0
    base_positions = [(row_start + i * spacing, 0.0) for i in range(count)]
    initial_locations = [
        (x + offset_x, y + offset_y, floor_z + half_height) for x, y in base_positions
    ]

    # The first tile starts pre-tilted past its critical tipping angle, at
    # rest (zero velocity) -- not kicked with an injected impulse -- so its
    # fall from frame 1 onward is pure, unforced gravity + contact physics,
    # exactly like every later tile's contact-triggered fall.
    identity_quat = (0.0, 0.0, 0.0, 1.0)
    push_angle_deg = float(args.push_angle_deg)
    push_quat = p.getQuaternionFromEuler((0.0, math.radians(push_angle_deg), 0.0))
    initial_orientations = [push_quat] + [identity_quat] * (count - 1)

    client = p.connect(p.DIRECT)
    try:
        p.resetSimulation(physicsClientId=client)
        p.setGravity(0.0, 0.0, float(args.gravity_z), physicsClientId=client)
        p.setTimeStep(dt, physicsClientId=client)
        p.setPhysicsEngineParameter(
            fixedTimeStep=dt,
            numSolverIterations=400,
            contactBreakingThreshold=0.0005,
            deterministicOverlappingPairs=1,
            enableConeFriction=1,
            physicsClientId=client,
        )

        floor_shape = p.createCollisionShape(
            p.GEOM_BOX,
            halfExtents=(12.0, 12.0, 0.05),
            physicsClientId=client,
        )
        floor_id = p.createMultiBody(
            0.0,
            floor_shape,
            -1,
            (offset_x, offset_y, floor_z - 0.05),
            physicsClientId=client,
        )
        p.changeDynamics(
            floor_id,
            -1,
            lateralFriction=float(args.floor_friction),
            restitution=0.1,
            physicsClientId=client,
        )

        domino_shape = p.createCollisionShape(
            p.GEOM_BOX,
            halfExtents=(thickness / 2.0, width / 2.0, half_height),
            physicsClientId=client,
        )
        domino_ids = []
        for location, orientation in zip(initial_locations, initial_orientations):
            domino_id = p.createMultiBody(
                baseMass=float(args.domino_mass),
                baseCollisionShapeIndex=domino_shape,
                baseVisualShapeIndex=-1,
                basePosition=location,
                baseOrientation=orientation,
                physicsClientId=client,
            )
            p.changeDynamics(
                domino_id,
                -1,
                lateralFriction=float(args.domino_friction),
                spinningFriction=0.01,
                rollingFriction=0.0005,
                restitution=float(args.domino_restitution),
                linearDamping=0.02,
                angularDamping=0.02,
                physicsClientId=client,
            )
            domino_ids.append(domino_id)

        frames = []
        max_tilt_deg = [0.0] * count

        for frame_index in range(1, frame_end + 1):
            if frame_index > 1:
                for _ in range(substeps):
                    p.stepSimulation(physicsClientId=client)

            domino_data = []
            for idx, domino_id in enumerate(domino_ids):
                dpos, dquat = p.getBasePositionAndOrientation(domino_id, physicsClientId=client)
                dlin, dang = p.getBaseVelocity(domino_id, physicsClientId=client)

                rot_matrix = p.getMatrixFromQuaternion(dquat)
                local_z_world_z = rot_matrix[8]  # dot of local Z axis with world Z
                tilt_deg = math.degrees(math.acos(max(-1.0, min(1.0, local_z_world_z))))
                max_tilt_deg[idx] = max(max_tilt_deg[idx], tilt_deg)

                domino_data.append({
                    "location": list(dpos),
                    "quaternion_xyzw": list(dquat),
                    "linear_velocity": list(dlin),
                    "angular_velocity": list(dang),
                    "tilt_deg": tilt_deg,
                })

            frames.append({
                "frame_index": frame_index,
                "time_sec": (frame_index - 1) / float(fps),
                "dominoes": domino_data,
            })

        toppled_count = sum(1 for tilt in max_tilt_deg if tilt > 45.0)

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
                "dominoes": {
                    "count": count,
                    "spacing": spacing,
                    "thickness": thickness,
                    "width": width,
                    "height": height,
                    "mass": float(args.domino_mass),
                    "initial_locations": [list(loc) for loc in initial_locations],
                    "initial_orientations_xyzw": [list(o) for o in initial_orientations],
                    "friction": float(args.domino_friction),
                    "restitution": float(args.domino_restitution),
                    "push_angle_deg": push_angle_deg,
                },
                "floor": {
                    "friction": float(args.floor_friction),
                    "z": floor_z,
                },
                "scene_offset": {
                    "x": offset_x,
                    "y": offset_y,
                },
            },
            "quality": {
                "max_tilt_deg_per_domino": max_tilt_deg,
                "toppled_count": toppled_count,
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
