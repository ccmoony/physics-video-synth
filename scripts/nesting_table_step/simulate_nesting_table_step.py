from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pybullet as p


# A marble is rolled across the taller of two nesting coffee tables, runs off
# the 47.6 mm step where the two tops meet, and strikes a second marble waiting
# on the lower one.
#
# The scene is built around one fact: **the step changes only the vertical
# motion.** Nothing acts on the marble horizontally while it is in the air, so
# its horizontal speed crosses the step untouched -- and so does its spin, since
# gravity applies no torque about the centre. What the drop adds is a vertical
# speed of sqrt(2 g h), which the lower table then takes back over two or three
# short bounces. The marble therefore arrives at the second one carrying the
# speed the *roll* gave it, not the speed the *fall* gave it, and the collision
# is decided upstairs.
#
# That is worth filming because the intuition runs the other way. A step looks
# like an accelerator: the ball drops, so surely it speeds up. It does -- but
# only downwards, and the downward part is spent on the table within a tenth of
# a second. The horizontal speed either side of the lip is the same number to
# within the solver's noise, and that is checked on every run.
#
# The predictions, all with no free parameters:
#
#     t_flight      = sqrt(2 h / g)                     = 0.0986 s
#     range         = v_lip * t_flight                   (from the lip)
#     v_z at landing= sqrt(2 g h)                        = 0.966 m/s
#     v_h at landing= v_lip                              (ratio 1.000)
#     omega landing = omega at the lip                   (ratio 1.000)
#     first hop     = e^2 * h
#
# and then the impact on the lower table, which is the same closed form the
# table_marble_collision scene is built on:
#
#     v_B = (1 + e) * m_A / (m_A + m_B) * v_A * cos(obliquity)
#
# With two identical marbles that is (1 + e)/2 = 0.88 of the approach speed for
# the struck one and (1 - e)/2 = 0.12 for the roller, which is the most legible
# outcome a two-ball collision has: the ball that did the travelling stops
# almost where it hit, and the one that was standing still leaves with nearly
# all of the speed.
#
# Coordinates are metric and match render_nesting_table_step.py's world frame:
# z = 0 is the *taller* table's top surface, the origin sits on the lip where
# the two tops meet, +x is the direction of travel (room west, toward the lower
# table) and +y is room south, toward the open side the camera stands on.

# --- the two table tops -------------------------------------------------------
#
# Measured off assets/models/modern_living_room.glb by raycasting, not taken
# from bounding boxes: every mesh in that model is named "Material2.0NN" and
# both tops belong to the *same* mesh, so a bounding box cannot tell them apart
# at all. Sub-millimetre edge walks put the lip at room x = 1.4573 with the two
# surfaces exactly flush against each other -- there is no gap between the
# tables for a marble to drop into, which is the only reason a 40 mm ball can
# cross this step safely.
HIGH_X = (-0.7616, 0.0000)
HIGH_Y = (-0.3808, 0.3808)
HIGH_TOP_Z = 0.0

LOW_X = (0.0000, 0.3558)
LOW_Y = (-0.3416, 0.3294)
LOW_TOP_Z = -0.0476          # the step: 47.6 mm, measured

STEP_HEIGHT = HIGH_TOP_Z - LOW_TOP_Z
PLATE = 0.030                # thickness given to each top as a collision box

FLOOR_Z = -0.2635            # the parquet, 263 mm below the taller top

SETTLE_SPEED = 0.02          # m/s below which a marble counts as stopped
SETTLE_SPIN = 0.30           # rad/s below which it counts as stopped *turning*
RESIDUAL_SPIN_LIMIT = 0.30

GLASS_DENSITY = 2500.0       # kg/m^3, soda-lime glass


def sphere_mass(radius: float, density: float = GLASS_DENSITY) -> float:
    return density * 4.0 / 3.0 * math.pi * radius ** 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--props-collider", type=Path, default=None,
        help="Metre-scale OBJ of everything standing on the two tops -- the "
        "candles and the tall vase on the lower one, the stack of books on the "
        "taller one -- written by render_nesting_table_step.py's --mode "
        "colliders pass. It is a separate body from the tables so that touching "
        "it is reported as its own event rather than silently deflecting the "
        "shot.",
    )
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument(
        "--playback-slowdown", type=float, default=1.0,
        help="Rendered frames emitted per real frame time; 1.0 is real time, "
        "which is what the hero take uses. Raising it samples the same "
        "simulation more finely and plays it back at --fps, which is the only "
        "way to see the flight frame by frame: the fall lasts sqrt(2h/g) = "
        "0.0986 s, so at 24 fps real time it is two frames and change.",
    )
    parser.add_argument("--duration-sec", type=float, default=1.7,
                        help="Physical seconds simulated, not seconds of video.")
    parser.add_argument(
        "--substeps", type=int, default=60,
        help="Physics steps per emitted frame. The impact and every bounce are "
        "resolved inside a single substep, and speeds either side of the lip "
        "and of each contact are sampled at substep resolution.",
    )
    parser.add_argument("--gravity-z", type=float, default=-9.8)

    # --- the marble that is rolled ---
    parser.add_argument("--ball-a-radius", type=float, default=0.020)
    parser.add_argument(
        "--ball-a-mass", type=float, default=None,
        help="Defaults to a solid glass sphere of --ball-a-radius at "
        f"{GLASS_DENSITY:.0f} kg/m^3. Pass a value only to break the link "
        "between the size you can see and the mass that does the work.",
    )
    parser.add_argument(
        "--ball-a-restitution", type=float, default=0.87,
        help="Bullet multiplies the two bodies' restitutions, so a ball-on-ball "
        "impact sees this times --ball-b-restitution, and a ball-on-table "
        "bounce sees this times --table-restitution.",
    )
    parser.add_argument("--ball-a-friction", type=float, default=0.30)

    # --- the marble that is struck ---
    parser.add_argument("--ball-b-radius", type=float, default=0.020)
    parser.add_argument("--ball-b-mass", type=float, default=None)
    parser.add_argument("--ball-b-restitution", type=float, default=0.87)
    parser.add_argument("--ball-b-friction", type=float, default=0.30)
    parser.add_argument(
        "--ball-b-x", type=float, default=0.190,
        help="Where the struck marble waits on the lower table, measured from "
        "the lip. Far enough out that the roller has finished bouncing and is "
        "rolling again before it arrives -- a collision caught mid-hop is not "
        "the collision the closed form describes.",
    )
    parser.add_argument("--ball-b-y", type=float, default=0.212)
    parser.add_argument(
        "--disable-ball-b", action="store_true",
        help="Leave the struck marble out. Used to calibrate the run-up and to "
        "measure the flight on its own: the speed at the lip has to be known "
        "before there is any point predicting where the marble lands.",
    )
    parser.add_argument("--ball-rolling-friction", type=float, default=0.0,
                        help="Kept at 0 so all rolling resistance comes from "
                        "the tops. Bullet combines rolling friction as "
                        "rf_a * lateral_b + rf_b * lateral_a, so giving a ball "
                        "rf = 1 multiplies in the *table's lateral* friction "
                        "instead and turns the roll into a skid.")
    parser.add_argument("--ball-spinning-friction", type=float, default=0.0)

    # --- surfaces ---
    parser.add_argument(
        "--table-rolling-friction", type=float, default=0.0060,
        help="Rolling resistance of both tops, and this shot's clock. Low: "
        "there is only 0.52 m of run-up and 0.36 m of lower table, and the "
        "marble has to still be moving when it gets to the far end. Bullet's "
        "rolling friction acts as a contact-offset length rather than a "
        "dimensionless coefficient, so the deceleration goes as 1/radius.",
    )
    parser.add_argument("--table-friction", type=float, default=0.42)
    parser.add_argument(
        "--table-restitution", type=float, default=0.34,
        help="Glass on a lacquered wooden top. With the marble at 0.87 the "
        "effective value for a bounce is 0.30, so the first hop off the lower "
        "table recovers 0.09 of the step's height -- about 4 mm, two or three "
        "bounces, and the marble is rolling again well before it reaches the "
        "second one.",
    )
    parser.add_argument("--table-spinning-friction", type=float, default=0.006)
    parser.add_argument("--props-friction", type=float, default=0.55)
    parser.add_argument("--props-restitution", type=float, default=0.20)
    parser.add_argument("--floor-friction", type=float, default=0.55)
    parser.add_argument("--floor-restitution", type=float, default=0.35)

    # --- the push ---
    parser.add_argument(
        "--launch-x", type=float, default=-0.480,
        help="Where the marble is set rolling on the taller top, measured back "
        "from the lip. The stack of books stands 0.52 m back, so this is very "
        "nearly all the run there is.",
    )
    parser.add_argument("--launch-y", type=float, default=0.220)
    parser.add_argument("--launch-speed", type=float, default=0.98)
    parser.add_argument("--launch-heading-deg", type=float, default=0.0)
    return parser.parse_args()


def add_box(client: int, half_extents, centre, friction: float, restitution: float,
            rolling_friction: float = 0.0, spinning_friction: float = 0.0) -> int:
    shape = p.createCollisionShape(p.GEOM_BOX, halfExtents=list(half_extents),
                                   physicsClientId=client)
    body = p.createMultiBody(0.0, shape, -1, list(centre), physicsClientId=client)
    p.changeDynamics(
        body, -1,
        lateralFriction=friction, rollingFriction=rolling_friction,
        spinningFriction=spinning_friction, restitution=restitution,
        # Bullet's default 0.04 m margin would inflate a box whose edge *is* the
        # step the marble is supposed to fall off by 40 mm -- as much as the
        # marble's own diameter, and nearly the height of the step itself.
        collisionMargin=0.0005, physicsClientId=client,
    )
    return body


def add_top(client: int, xs, ys, top_z: float, args: argparse.Namespace) -> int:
    """One of the two tops, as a box whose upper face sits at ``top_z``.

    Boxes rather than the model's own triangles, and here that is the honest
    choice: both tops are dead-flat rectangles -- the raycast returns the same z
    to four decimals across each of them -- and the model's mesh carries the
    black steel frame underneath, which no marble on either top can reach.
    """
    half = ((xs[1] - xs[0]) / 2.0, (ys[1] - ys[0]) / 2.0, PLATE / 2.0)
    centre = ((xs[0] + xs[1]) / 2.0, (ys[0] + ys[1]) / 2.0, top_z - PLATE / 2.0)
    return add_box(client, half, centre, float(args.table_friction),
                   float(args.table_restitution),
                   float(args.table_rolling_friction),
                   float(args.table_spinning_friction))


def add_floor(client: int, args: argparse.Namespace) -> int:
    """The parquet, so a marble that leaves either top lands on something."""
    return add_box(client, (3.0, 3.0, 0.25), (0.0, 0.0, FLOOR_Z - 0.25),
                   float(args.floor_friction), float(args.floor_restitution))


def add_props(client: int, obj_path: Path, friction: float, restitution: float) -> int:
    if not obj_path.exists():
        raise FileNotFoundError(
            f"Table-top dressing collision mesh not found: {obj_path}\n"
            "Generate it first with:\n"
            "  blender -b --python scripts/nesting_table_step/"
            "render_nesting_table_step.py -- --mode colliders --out-dir /tmp/colliders"
        )
    shape = p.createCollisionShape(
        p.GEOM_MESH, fileName=str(obj_path), meshScale=[1.0, 1.0, 1.0],
        flags=p.GEOM_FORCE_CONCAVE_TRIMESH, physicsClientId=client,
    )
    body = p.createMultiBody(0.0, shape, -1, [0.0, 0.0, 0.0], physicsClientId=client)
    p.changeDynamics(body, -1, lateralFriction=friction, rollingFriction=0.0,
                     spinningFriction=0.0, restitution=restitution,
                     collisionMargin=0.0005, physicsClientId=client)
    return body


def add_ball(client: int, radius: float, mass: float, position, friction: float,
             restitution: float, rolling_friction: float,
             spinning_friction: float) -> int:
    shape = p.createCollisionShape(p.GEOM_SPHERE, radius=radius, physicsClientId=client)
    body = p.createMultiBody(mass, shape, -1, list(position), physicsClientId=client)
    p.changeDynamics(
        body, -1,
        lateralFriction=friction, rollingFriction=rolling_friction,
        spinningFriction=spinning_friction, restitution=restitution,
        linearDamping=0.0, angularDamping=0.0,
        collisionMargin=0.0005, physicsClientId=client,
    )
    return body


def horiz(v) -> float:
    return math.hypot(v[0], v[1])


def norm3(v) -> float:
    return math.sqrt(sum(c ** 2 for c in v))


def heading_deg(v) -> float:
    return math.degrees(math.atan2(v[1], v[0])) % 360.0


def signed_turn(before, after) -> float:
    if horiz(before) < 1e-9 or horiz(after) < 1e-9:
        return float("nan")
    a = math.atan2(before[1], before[0])
    b = math.atan2(after[1], after[0])
    return math.degrees((b - a + math.pi) % (2.0 * math.pi) - math.pi)


def predict_flight(v_lip: float, v_z_lip: float, drop: float, height: float,
                   g: float) -> dict:
    """The closed form the flight off the step is checked against.

    Once the marble is unsupported the only force on it is its weight: the
    horizontal velocity is constant, the vertical one grows as g t, and gravity
    acting through the centre of mass applies no torque, so the spin is constant
    too. Every one of those is a prediction with no free parameter in it.

    Two versions of the timing are returned, and the difference between them is
    not noise -- it is a real feature of rolling off a step. A marble does not
    step off a lip cleanly: it pivots about the corner while still touching it,
    so by the time it separates its centre has already dropped a little and it
    is already moving downwards. The **ideal** figures are the textbook ones for
    a body released from rest at the top surface. The **corrected** figures use
    the vertical speed and the drop the marble actually had at separation, and
    are what the measurement should match:

        t = (-v_z0 + sqrt(v_z0^2 + 2 g dz)) / g

    The one number that does not care which of the two you use is the vertical
    speed on landing. Whether the marble spends the first millimetre of the drop
    pivoting or falling free, it arrives having converted the whole step height
    into speed, so sqrt(2 g h) is exact either way -- which is why that is the
    check with the tight tolerance on it.
    """
    ideal_t = math.sqrt(2.0 * height / g)
    v0 = max(0.0, -v_z_lip)
    dz = max(1e-9, drop)
    t = (-v0 + math.sqrt(v0 * v0 + 2.0 * g * dz)) / g
    return {
        "flight_time": t,
        "range_from_lip": v_lip * t,
        "landing_vertical_speed": math.sqrt(2.0 * g * height),
        "landing_horizontal_speed": v_lip,
        "horizontal_speed_ratio": 1.0,
        "spin_ratio": 1.0,
        "separation_vertical_speed": v0,
        "drop_after_separation": dz,
        "ideal": {
            "flight_time": ideal_t,
            "range_from_lip": v_lip * ideal_t,
            "note": "released from rest at the top surface; the marble instead "
                    "pivots about the lip and separates already falling.",
        },
    }


def predict_impact(m_a: float, m_b: float, e: float, obliquity_deg: float,
                   side: float) -> dict:
    """The closed form the collision is checked against, for an oblique impact.

    Two smooth spheres exchange momentum only along the line joining their
    centres; the component across it is untouched. Working in a frame whose x
    axis is the roller's approach, with the centre line at ``obliquity`` off it
    on the ``side`` the struck marble sits:

        n    = (cos t, side * sin t)
        v_B' = nu * v cos t * n           nu  = (1 + e) m_A / (m_A + m_B)
        v_A' = v x_hat - lam * v cos t * n lam = (1 + e) m_B / (m_A + m_B)

    The struck marble therefore leaves *exactly* along the centre line whatever
    the masses are. Doing this properly rather than as ``1 - lam cos t`` matters
    for equal masses, which is this scene's default: the approximation puts the
    roller's outgoing speed at 0.065 of its approach when the correct value is
    0.12 at 10 deg of obliquity.
    """
    t = math.radians(obliquity_deg)
    cos_t, sin_t = math.cos(t), math.sin(t)
    nu = (1.0 + e) * m_a / (m_a + m_b)
    lam = (1.0 + e) * m_b / (m_a + m_b)

    b_along, b_across = nu * cos_t * cos_t, nu * cos_t * side * sin_t
    a_along = 1.0 - lam * cos_t * cos_t
    a_across = -lam * cos_t * side * sin_t

    a_turn = math.degrees(math.atan2(a_across, a_along))
    b_turn = math.degrees(math.atan2(b_across, b_along))
    return {
        "b_speed_ratio": math.hypot(b_along, b_across),
        "a_speed_ratio": math.hypot(a_along, a_across),
        "a_along_approach_ratio": a_along,
        "a_reverses": a_along < 0.0,
        "a_turn_deg": a_turn,
        "b_turn_deg": b_turn,
        "separation_deg": abs(a_turn - b_turn),
    }


def surface_under(x: float, y: float):
    """Which top a point is over: 'high', 'low' or None."""
    if HIGH_X[0] <= x <= HIGH_X[1] and HIGH_Y[0] <= y <= HIGH_Y[1]:
        return "high"
    if LOW_X[0] <= x <= LOW_X[1] and LOW_Y[0] <= y <= LOW_Y[1]:
        return "low"
    return None


def off_tables(position):
    """Which edge a marble's centre has crossed, if it has left both tops."""
    x, y = position[0], position[1]
    if surface_under(x, y) is not None:
        return None
    if x > LOW_X[1]:
        return "west (off the far end of the lower table, onto the rug)"
    if x < HIGH_X[0]:
        return "east (off the far end of the taller table)"
    if y > max(HIGH_Y[1], LOW_Y[1]):
        return "south (the open side the camera stands on)"
    if y < min(HIGH_Y[0], LOW_Y[0]):
        return "north (toward the sofa)"
    return "off both tops"


def simulate(args: argparse.Namespace) -> dict:
    fps = int(args.fps)
    slowdown = max(1.0, float(args.playback_slowdown))
    sample_rate = fps * slowdown            # emitted frames per physical second
    frame_end = max(2, int(round(float(args.duration_sec) * sample_rate)))
    substeps = int(args.substeps)
    dt = 1.0 / (sample_rate * substeps)
    g = abs(float(args.gravity_z))

    r_a = float(args.ball_a_radius)
    r_b = float(args.ball_b_radius)
    m_a = sphere_mass(r_a) if args.ball_a_mass is None else float(args.ball_a_mass)
    m_b = sphere_mass(r_b) if args.ball_b_mass is None else float(args.ball_b_mass)
    e_ball = float(args.ball_a_restitution) * float(args.ball_b_restitution)
    e_bounce = float(args.ball_a_restitution) * float(args.table_restitution)

    heading = math.radians(float(args.launch_heading_deg))
    speed = float(args.launch_speed)
    velocity = (speed * math.cos(heading), speed * math.sin(heading), 0.0)
    # Launched already rolling rather than sliding: omega = (z_hat x v) / r.
    # From rest the marble spends its first tenth of a second skidding while
    # friction spins it up, which reads as a shove and not a roll -- and it
    # would also wreck the scene's point, which is that the rolling condition
    # survives the step.
    spin = (-velocity[1] / r_a, velocity[0] / r_a, 0.0)

    ball_a_start = (float(args.launch_x), float(args.launch_y), r_a)
    ball_b_start = (float(args.ball_b_x), float(args.ball_b_y), LOW_TOP_Z + r_b)
    impact_parameter = abs(ball_a_start[1] - ball_b_start[1])
    separation = r_a + r_b
    obliquity_deg = math.degrees(math.asin(min(1.0, impact_parameter / separation)))

    client = p.connect(p.DIRECT)
    try:
        p.resetSimulation(physicsClientId=client)
        p.setGravity(0.0, 0.0, float(args.gravity_z), physicsClientId=client)
        p.setTimeStep(dt, physicsClientId=client)
        p.setPhysicsEngineParameter(
            fixedTimeStep=dt,
            numSolverIterations=300,
            contactBreakingThreshold=0.001,
            deterministicOverlappingPairs=1,
            enableConeFriction=1,
            physicsClientId=client,
        )

        high_id = add_top(client, HIGH_X, HIGH_Y, HIGH_TOP_Z, args)
        low_id = add_top(client, LOW_X, LOW_Y, LOW_TOP_Z, args)
        add_floor(client, args)
        props_id = None
        if args.props_collider is not None:
            props_id = add_props(client, args.props_collider,
                                 float(args.props_friction),
                                 float(args.props_restitution))

        ball_a = add_ball(client, r_a, m_a, ball_a_start,
                          float(args.ball_a_friction), float(args.ball_a_restitution),
                          float(args.ball_rolling_friction),
                          float(args.ball_spinning_friction))
        ball_b = None
        if not args.disable_ball_b:
            ball_b = add_ball(client, r_b, m_b, ball_b_start,
                              float(args.ball_b_friction),
                              float(args.ball_b_restitution),
                              float(args.ball_rolling_friction),
                              float(args.ball_spinning_friction))

        p.resetBaseVelocity(ball_a, linearVelocity=velocity, angularVelocity=spin,
                            physicsClientId=client)

        flight = {
            "left_lip_time": None, "left_lip_frame": None, "left_lip_position": None,
            "lip_velocity": None, "lip_spin": None,
            "landed_time": None, "landed_frame": None, "landed_position": None,
            "landing_velocity": None, "landing_spin": None,
            "apex_drop": None, "hops": 0, "first_hop_height": None,
        }
        airborne = False
        touching_low = False
        hop_launch_z = None

        hit = {"frame": None, "time": None, "point": None, "normal": None,
               "a_speed_in": None, "a_speed_out": None, "b_speed_out": None,
               "a_vel_in": None, "a_vel_out": None, "b_vel_out": None}
        touching_ball = False
        prop_contact_frame = None
        left_tables = None

        frames = []
        settle_a = None
        settle_b = None
        prev_a_vel = velocity
        prev_a_spin = spin

        for frame_index in range(1, frame_end + 1):
            for sub in range(substeps if frame_index > 1 else 0):
                p.stepSimulation(physicsClientId=client)
                # Substep time, not frame time. The whole flight is 0.0986 s,
                # which is two frames and a fifth at 24 fps: timing it to the
                # nearest frame quantises it to 0.0833 s and reports a 13 per
                # cent disagreement with a closed form that is in fact exact.
                now = ((frame_index - 2) + (sub + 1) / substeps) / sample_rate

                a_pos, _ = p.getBasePositionAndOrientation(ball_a, physicsClientId=client)
                a_vel, a_spin = p.getBaseVelocity(ball_a, physicsClientId=client)

                on_high = bool(p.getContactPoints(bodyA=ball_a, bodyB=high_id,
                                                  physicsClientId=client))
                on_low = bool(p.getContactPoints(bodyA=ball_a, bodyB=low_id,
                                                 physicsClientId=client))

                # Leaving the lip: the substep on which the taller top stops
                # touching the marble and the lower one has not started. The
                # velocity and spin recorded here are the two quantities the
                # flight is not allowed to change.
                if not airborne and not on_high and not on_low and a_vel[2] <= 0.0:
                    airborne = True
                    flight["left_lip_time"] = now
                    flight["left_lip_frame"] = frame_index
                    flight["left_lip_position"] = list(a_pos)
                    flight["lip_velocity"] = list(prev_a_vel)
                    flight["lip_spin"] = list(prev_a_spin)
                    hop_launch_z = a_pos[2]

                if airborne and on_low and not touching_low:
                    touching_low = True
                    flight["hops"] += 1
                    if flight["landed_time"] is None:
                        flight["landed_time"] = now
                        flight["landed_frame"] = frame_index
                        flight["landed_position"] = list(a_pos)
                        flight["landing_velocity"] = list(prev_a_vel)
                        flight["landing_spin"] = list(prev_a_spin)
                elif touching_low and not on_low:
                    touching_low = False
                    hop_launch_z = a_pos[2]
                elif touching_low:
                    hop_launch_z = None

                if (flight["landed_time"] is not None and not on_low
                        and hop_launch_z is not None):
                    rise = a_pos[2] - hop_launch_z
                    if flight["first_hop_height"] is None or rise > flight["first_hop_height"]:
                        if flight["hops"] == 1:
                            flight["first_hop_height"] = rise

                if ball_b is not None:
                    # Only the *first* touch is the scene's impact.
                    points = p.getContactPoints(bodyA=ball_a, bodyB=ball_b,
                                                physicsClientId=client)
                    if points and not touching_ball:
                        touching_ball = True
                        if hit["frame"] is None:
                            cp = max(points, key=lambda c: -c[8])
                            hit["frame"] = frame_index
                            hit["time"] = now
                            hit["point"] = list(cp[5])
                            hit["normal"] = list(cp[7])
                            hit["a_speed_in"] = horiz(prev_a_vel)
                            hit["a_vel_in"] = list(prev_a_vel)
                    elif not points and touching_ball:
                        touching_ball = False
                        if hit["a_speed_out"] is None:
                            b_vel, _ = p.getBaseVelocity(ball_b, physicsClientId=client)
                            hit["a_speed_out"] = horiz(a_vel)
                            hit["b_speed_out"] = horiz(b_vel)
                            hit["a_vel_out"] = list(a_vel)
                            hit["b_vel_out"] = list(b_vel)

                if props_id is not None and prop_contact_frame is None:
                    for body in (ball_a, ball_b):
                        if body is None:
                            continue
                        if p.getContactPoints(bodyA=body, bodyB=props_id,
                                              physicsClientId=client):
                            prop_contact_frame = frame_index
                            break

                prev_a_vel = a_vel
                prev_a_spin = a_spin

            record = {"frame_index": frame_index, "time_sec": (frame_index - 1) / sample_rate}
            spin_now = {}
            for name, body, radius in (("ball_a", ball_a, r_a), ("ball_b", ball_b, r_b)):
                if body is None:
                    continue
                pos, quat = p.getBasePositionAndOrientation(body, physicsClientId=client)
                lin, ang = p.getBaseVelocity(body, physicsClientId=client)
                surface = surface_under(pos[0], pos[1])
                record[name] = {
                    "location": list(pos),
                    "quaternion_xyzw": list(quat),
                    "linear_velocity": list(lin),
                    "angular_velocity": list(ang),
                    "speed": norm3(lin),
                    "horizontal_speed": horiz(lin),
                    "spin_z": ang[2],
                    "spin": norm3(ang),
                    "surface": surface,
                    "on_table": surface is not None and pos[2] > FLOOR_Z + 0.05,
                }
                spin_now[name] = norm3(ang)
                edge = off_tables(pos)
                if edge is not None and left_tables is None:
                    left_tables = {"ball": name, "frame": frame_index, "edge": edge,
                                   "location": list(pos)}

            # The roller's phase, so the video and the ground truth agree about
            # which part of the shot each frame belongs to.
            if flight["left_lip_frame"] is None or frame_index < flight["left_lip_frame"]:
                phase = "rolling on the taller top"
            elif flight["landed_frame"] is None or frame_index < flight["landed_frame"]:
                phase = "in flight"
            elif hit["frame"] is None or frame_index < hit["frame"]:
                phase = "on the lower top"
            else:
                phase = "after impact"
            record["ball_a"]["phase"] = phase

            a_still = (record["ball_a"]["speed"] < SETTLE_SPEED
                       and spin_now["ball_a"] < SETTLE_SPIN)
            settle_a = (frame_index if settle_a is None else settle_a) if a_still else None
            if ball_b is not None:
                b_still = (record["ball_b"]["speed"] < SETTLE_SPEED
                           and spin_now["ball_b"] < SETTLE_SPIN)
                if hit["frame"] is not None and b_still:
                    settle_b = frame_index if settle_b is None else settle_b
                elif not b_still:
                    settle_b = None
                record["ball_b"]["moving"] = bool(
                    hit["frame"] is not None and frame_index >= hit["frame"])
            frames.append(record)

        last = frames[-1]
        residual_spin = {name: norm3(last[name]["angular_velocity"])
                         for name in ("ball_a", "ball_b") if name in last}

        v_lip = horiz(flight["lip_velocity"]) if flight["lip_velocity"] else None
        prediction_flight = None
        if v_lip is not None and flight["landed_position"] is not None:
            prediction_flight = predict_flight(
                v_lip, flight["lip_velocity"][2],
                flight["left_lip_position"][2] - flight["landed_position"][2],
                STEP_HEIGHT, g)
        measured_flight = None
        if flight["landed_time"] is not None and flight["left_lip_time"] is not None:
            measured_flight = {
                "flight_time": flight["landed_time"] - flight["left_lip_time"],
                "range_from_lip": math.dist(
                    (flight["landed_position"][0], flight["landed_position"][1]),
                    (flight["left_lip_position"][0], flight["left_lip_position"][1])),
                "landing_vertical_speed": abs(flight["landing_velocity"][2]),
                "landing_horizontal_speed": horiz(flight["landing_velocity"]),
                "horizontal_speed_ratio": (horiz(flight["landing_velocity"]) / v_lip
                                           if v_lip else None),
                "spin_ratio": (norm3(flight["landing_spin"]) / norm3(flight["lip_spin"])
                               if norm3(flight["lip_spin"]) > 1e-9 else None),
            }

        approach_run = None
        if hit["frame"] is not None:
            contact = frames[hit["frame"] - 1]["ball_a"]["location"]
            approach_run = math.dist((ball_a_start[0], ball_a_start[1]),
                                     (contact[0], contact[1]))
        travel = {}
        for name, start in (("ball_a", ball_a_start), ("ball_b", ball_b_start)):
            if name not in last:
                continue
            travel[name] = math.dist(
                (last[name]["location"][0], last[name]["location"][1]),
                (start[0], start[1]))

        prediction_impact = predict_impact(
            m_a, m_b, e_ball, obliquity_deg,
            1.0 if ball_b_start[1] >= ball_a_start[1] else -1.0)
        measured_b = (None if hit["b_speed_out"] is None or not hit["a_speed_in"]
                      else hit["b_speed_out"] / hit["a_speed_in"])
        measured_a = (None if hit["a_speed_out"] is None or not hit["a_speed_in"]
                      else hit["a_speed_out"] / hit["a_speed_in"])

        return {
            "schema_version": 1,
            "simulator": "pybullet",
            "fps": fps,
            "playback_slowdown": slowdown,
            "sample_rate_hz": sample_rate,
            "frame_start": 1,
            "frame_end": frame_end,
            "duration_sec": float(args.duration_sec),
            "video_duration_sec": frame_end / float(fps),
            "substeps_per_frame": substeps,
            "physics_dt": dt,
            "objects": {
                "ball_a": {
                    "radius": r_a, "mass": m_a, "diameter": 2.0 * r_a,
                    "initial_location": list(ball_a_start),
                    "initial_linear_velocity": list(velocity),
                    "initial_angular_velocity": list(spin),
                    "restitution": float(args.ball_a_restitution),
                    "friction": float(args.ball_a_friction),
                },
                "ball_b": {
                    "radius": r_b, "mass": m_b, "diameter": 2.0 * r_b,
                    "initial_location": list(ball_b_start),
                    "restitution": float(args.ball_b_restitution),
                    "friction": float(args.ball_b_friction),
                },
                "high_top": {"footprint_x": list(HIGH_X), "footprint_y": list(HIGH_Y),
                             "top_z": HIGH_TOP_Z},
                "low_top": {"footprint_x": list(LOW_X), "footprint_y": list(LOW_Y),
                            "top_z": LOW_TOP_Z},
                "step_height": STEP_HEIGHT,
                "surface": {
                    "friction": float(args.table_friction),
                    "rolling_friction": float(args.table_rolling_friction),
                    "spinning_friction": float(args.table_spinning_friction),
                    "restitution": float(args.table_restitution),
                },
                "floor_z": FLOOR_Z,
            },
            "step": {
                "height": STEP_HEIGHT,
                "effective_bounce_restitution": e_bounce,
                "predicted": prediction_flight,
                "measured": measured_flight,
                "left_lip_frame": flight["left_lip_frame"],
                "left_lip_time": flight["left_lip_time"],
                "left_lip_position": flight["left_lip_position"],
                "lip_velocity": flight["lip_velocity"],
                "lip_spin": flight["lip_spin"],
                "landed_frame": flight["landed_frame"],
                "landed_time": flight["landed_time"],
                "landed_position": flight["landed_position"],
                "landing_velocity": flight["landing_velocity"],
                "landing_spin": flight["landing_spin"],
                "bounces_on_lower_top": flight["hops"],
                "first_hop_height": flight["first_hop_height"],
                "predicted_first_hop_height": e_bounce ** 2 * STEP_HEIGHT,
            },
            "collision": {
                "mass_ratio": m_a / m_b,
                "size_ratio": r_a / r_b,
                "effective_restitution": e_ball,
                "impact_parameter": impact_parameter,
                "centre_separation": separation,
                "obliquity_deg": obliquity_deg,
                "predicted": prediction_impact,
                "measured_b_speed_ratio": measured_b,
                "measured_a_speed_ratio": measured_a,
            },
            "quality": {
                "made_the_step": flight["landed_frame"] is not None,
                "hit_ball_b": hit["frame"] is not None,
                "contact_frame": hit["frame"],
                "contact_time": hit["time"],
                "contact_point": hit["point"],
                "contact_normal": hit["normal"],
                "still_bouncing_at_impact": bool(
                    hit["frame"] is not None and flight["landed_frame"] is not None
                    and hit["frame"] - flight["landed_frame"] < 2),
                "approach_run": approach_run,
                "a_speed_in": hit["a_speed_in"],
                "a_speed_out": hit["a_speed_out"],
                "b_speed_out": hit["b_speed_out"],
                "a_heading_in_deg": (None if hit["a_vel_in"] is None
                                     else heading_deg(hit["a_vel_in"])),
                "a_turn_deg": (None if hit["a_vel_out"] is None
                               else signed_turn(hit["a_vel_in"], hit["a_vel_out"])),
                "b_heading_deg": (None if hit["b_vel_out"] is None
                                  else heading_deg(hit["b_vel_out"])),
                "separation_deg": (
                    None if hit["a_vel_out"] is None or hit["b_vel_out"] is None
                    else abs(signed_turn(hit["a_vel_out"], hit["b_vel_out"]))),
                "ball_a_final": last["ball_a"]["location"],
                "ball_b_final": None if ball_b is None else last["ball_b"]["location"],
                "ball_a_travel": travel.get("ball_a"),
                "ball_b_travel": travel.get("ball_b"),
                "ball_a_settled_frame": settle_a,
                "ball_b_settled_frame": settle_b,
                "residual_spin": residual_spin,
                "spinning_on_the_spot": any(v > RESIDUAL_SPIN_LIMIT
                                            for v in residual_spin.values()),
                "hit_table_dressing": prop_contact_frame is not None,
                "prop_contact_frame": prop_contact_frame,
                "left_tables": left_tables,
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
    q, s, c = records["quality"], records["step"], records["collision"]

    def fmt(value, spec="%.3f"):
        return "None" if value is None else spec % value

    sp, sm = s["predicted"], s["measured"]
    print(
        "[SIM] step=%s hit=%s | lip f=%s v=%s | flight %s/%s s  range %s/%s m | "
        "land vz=%s/%s vh=%s/%s spin_ratio=%s | hops=%s hop1=%s/%s | "
        "impact f=%s a_in=%s -> a=%s/%s b=%s/%s | A_end=(%.3f, %.3f) B_end=%s | "
        "settle=(%s, %s) props=%s off=%s" % (
            q["made_the_step"], q["hit_ball_b"],
            s["left_lip_frame"], fmt(horiz(s["lip_velocity"]) if s["lip_velocity"] else None),
            fmt(sm["flight_time"] if sm else None, "%.4f"),
            fmt(sp["flight_time"] if sp else None, "%.4f"),
            fmt(sm["range_from_lip"] if sm else None),
            fmt(sp["range_from_lip"] if sp else None),
            fmt(sm["landing_vertical_speed"] if sm else None),
            fmt(sp["landing_vertical_speed"] if sp else None),
            fmt(sm["landing_horizontal_speed"] if sm else None),
            fmt(sp["landing_horizontal_speed"] if sp else None),
            fmt(sm["spin_ratio"] if sm else None),
            s["bounces_on_lower_top"],
            fmt(s["first_hop_height"], "%.4f"),
            fmt(s["predicted_first_hop_height"], "%.4f"),
            q["contact_frame"], fmt(q["a_speed_in"]),
            fmt(c["measured_a_speed_ratio"]), fmt(c["predicted"]["a_speed_ratio"]),
            fmt(c["measured_b_speed_ratio"]), fmt(c["predicted"]["b_speed_ratio"]),
            q["ball_a_final"][0], q["ball_a_final"][1],
            None if q["ball_b_final"] is None else
            "(%.3f, %.3f)" % (q["ball_b_final"][0], q["ball_b_final"][1]),
            q["ball_a_settled_frame"], q["ball_b_settled_frame"],
            q["prop_contact_frame"],
            None if q["left_tables"] is None else
            f"{q['left_tables']['ball']}@{q['left_tables']['frame']}"
            f" {q['left_tables']['edge']}",
        )
    )


if __name__ == "__main__":
    main()
