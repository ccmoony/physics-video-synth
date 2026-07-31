"""Blender/Cycles renderer for the marble that runs off the step between two
nesting coffee tables.

Imports assets/models/modern_living_room.glb, calibrates it into the physics
world frame, puts two glass marbles on the living room's pair of nesting coffee
tables, runs simulate_nesting_table_step.py and replays the result as keyframes.

Run through Blender, not python:

    blender -b --python render_nesting_table_step.py -- --out-dir ...
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import random
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import bpy
from mathutils import Matrix, Vector


WORKSPACE_DIR = Path(__file__).resolve().parents[2]
MODELS_DIR = WORKSPACE_DIR / "assets" / "models"
POLYHAVEN_DIR = WORKSPACE_DIR / "assets" / "polyhaven"
COLLISION_DIR = WORKSPACE_DIR / "assets" / "collision"

ROOM_GLB = MODELS_DIR / "modern_living_room.glb"
MARBLE_GLB = MODELS_DIR / "marble_yellow_ball.glb"
PROPS_COLLIDER_OBJ = COLLISION_DIR / "modern_living_room_nesting_tops.obj"

OUTPUT_STEM = "nesting_table_step"
BLEND_NAME = f"{OUTPUT_STEM}.blend"
PHYSICS_TEMP_NAME = "physics_transforms.json"
GROUND_TRUTH_NAME = "ground_truth_transforms.json"
SCENARIO_METADATA_NAME = "scenario_metadata.json"


# --- Room model calibration ---------------------------------------------------
#
# assets/models/modern_living_room.glb is a SketchUp-style export: it is not in
# metres, and every one of its 124 meshes is named "Material2.0NN", so nothing
# in it can be found by name. Worse for this scene than for the others: **both
# nesting tops are the same mesh**, so even a bounding box cannot tell the
# taller table from the shorter one. Every number below was measured by
# raycasting the imported model, and verify_tops() re-measures them at import
# time rather than trusting them.
#
# The unit is fixed by the one dimension in a kitchen that is standardised: the
# worktop. It sits at model z = 37.219, and the kitchen's bar table top is the
# same flush surface, so taking it as 0.90 m fixes the scale. Four independent
# checks agree -- the ceiling lands at 2.571 m, the window sill at 1.058 m, the
# sofa's seat at 0.421 m and the bar stools' seats at 0.718 m. See
# scripts/table_marble_collision/ for the derivation; this scene inherits it.
ROOM_TABLE_UNITS = 37.219
BAR_TABLE_HEIGHT_M = 0.900
ROOM_SCALE = BAR_TABLE_HEIGHT_M / ROOM_TABLE_UNITS

# The room frame that calibration produces: z = 0 at the bar table top, origin
# at the centre of it, +x east, +y north. This scene does not use that frame --
# its subject is 3 m away in the living room and running the other way -- so the
# room is moved into a frame built on the step instead. The three numbers below
# are that step, in room-frame metres, from sub-millimetre edge walks:
#
#   - the lip where the two tops meet is at room x = 1.4573, and the two
#     surfaces are exactly flush there -- no gap at all, which is the only
#     reason a 40 mm marble can cross this step rather than drop into it;
#   - the taller top runs room y 1.2225 .. 1.9841, so its centre line is 1.6033;
#   - the taller top's surface is at room z = -0.6365, i.e. 263.5 mm above the
#     parquet, and the shorter one at -0.6841, i.e. 215.9 mm.
ROOM_LIP_X = 1.4573
ROOM_CENTRE_Y = 1.6033
ROOM_HIGH_TOP_Z = -0.6365

# The scene frame, which is the room frame rotated a half turn about z and
# shifted onto the lip:
#
#   z = 0   is the taller table's top surface,
#   (0, 0)  is on the lip, on the taller top's centre line,
#   +x      is the direction of travel -- room west, from the taller table
#           toward the shorter one,
#   +y      is room south, the open side the camera stands on,
#   +z      is up.
#
# The half turn is what makes every number in this scene read forwards: the
# marble launches at negative x, crosses the lip at x = 0 and lands at positive
# x. Rotating is free -- it is a proper rotation, so nothing is mirrored -- and
# the alternative is a scene whose subject travels in -x and whose README has a
# minus sign in front of every distance.
ROOM_TO_SCENE_YAW = math.pi

# The two tops in scene metres, from the same edge walks. Re-derived at import
# time by verify_tops() and warned about if the model has changed under us.
HIGH_X = (-0.7616, 0.0000)
HIGH_Y = (-0.3808, 0.3808)
LOW_X = (0.0000, 0.3558)
LOW_Y = (-0.3416, 0.3294)
LOW_TOP_Z = -0.0476
STEP_HEIGHT = -LOW_TOP_Z
FLOOR_Z = -0.2635
VERIFY_TOLERANCE = 0.004

# Everything standing on the two tops goes into the collider, and it is picked
# out by *region* rather than by name: every vertex above either top surface
# inside the box below is kept, everything else is dropped. That sidesteps the
# meaningless names entirely -- the collider is "the dressing" by construction
# rather than by a list of guesses -- and it also drops the tops themselves,
# which are modelled as boxes in the simulation and would otherwise be in twice.
PROPS_REGION_X = (-0.80, 0.40)
PROPS_REGION_Y = (-0.42, 0.42)
PROPS_REGION_Z = (-0.05, 0.45)
PROPS_CLEARANCE = 0.0015     # how far above a top a vertex must be to be kept

# The room's ceiling carries a large recessed luminous panel spanning nearly the
# whole flat, modelled as a housing, a warm yellow face and a grey translucent
# diffuser, all with ordinary diffuse materials and no emission -- this model
# ships no light objects of any kind. Cycles renders that as what it
# geometrically is: a dull mustard slab with the room black underneath. The face
# is given a real emission shader on its own copy of the material (material_46
# is shared, and rewriting the shared datablock would set fire to everything
# else using it) and the diffuser is hidden.
CEILING_PANEL_FACE = "Material2.043"
CEILING_PANEL_DIFFUSER = "Material2.095"
CEILING_PANEL_COLOR = (1.0, 0.955, 0.88)

# Both marbles are 40 mm and both are the same glass, which is the point: with
# equal masses the collision is as legible as a two-ball collision gets -- the
# ball that did the travelling stops almost dead and the one that was standing
# still leaves with nearly all of the speed -- so nothing in the impact competes
# with the step for the viewer's attention.
#
# 40 mm against a 47.6 mm step is the size that reads: the marble falls a little
# more than its own diameter, which is unmistakable on screen, while still
# leaving the 0.36 m shorter table eight diameters wide.
BALL_A_DIAMETER = 0.040
BALL_B_DIAMETER = 0.040

# Framing: low and nearly side-on from the room's open south side, 0.42 m off
# the parquet -- 0.16 m above the taller top. The subject is a 47.6 mm drop, and
# a step only reads as a step in profile: from anywhere above, the two tops
# flatten into one plane and the marble appears to change size rather than
# height. The camera is therefore barely above the surface it is watching.
#
# The open side is also the only side. The sofa wraps the tables along the north
# and the east, the tall vase stands on the shorter top's north half, and the
# stack of books occupies the taller top's south-east corner; south is where a
# camera fits.
CAMERA_LOCATION = (-0.10, 0.95, 0.17)
CAMERA_TARGET = (0.03, 0.06, -0.03)
CAMERA_LENS_MM = 50.0


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=("preview", "animation", "frames", "colliders"),
        default="animation",
        help="'colliders' only rebuilds assets/collision/*.obj and exits, which "
        "is all simulate_nesting_table_step.py needs to run standalone.",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--resolution", nargs=2, type=int, default=(1280, 720))
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument(
        "--playback-slowdown", type=float, default=1.0,
        help="Rendered frames per real frame time; 1.0 is real time. See the "
        "simulation's own help for when raising it is worth it.",
    )
    parser.add_argument("--duration-sec", type=float, default=1.7,
                        help="Physical seconds, not seconds of video.")
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--preview-frame", type=int, default=24)
    parser.add_argument(
        "--preview-frames", nargs="+", type=int, default=None,
        help="Render several stills instead of one, as preview_f<N>.png. "
        "Preview mode only; this is what cam_survey.py drives.",
    )
    parser.add_argument("--device", choices=("auto", "cpu"), default="auto")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--refresh-colliders", action="store_true")
    parser.add_argument("--scenario-json", type=Path, default=None)
    parser.add_argument("--scenario-overrides-json", type=Path, default=None)
    return parser.parse_args(argv)


# --- Scenario -----------------------------------------------------------------

def deep_merge(base: dict, overrides: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def build_scenario(args: argparse.Namespace) -> dict[str, Any]:
    if args.scenario_json is not None:
        return json.loads(args.scenario_json.read_text(encoding="utf-8"))
    scenario = {
        "schema_version": 1,
        "seed": int(args.seed),
        "scene": OUTPUT_STEM,
        "render": {
            "mode": str(args.mode),
            "fps": int(args.fps),
            "playback_slowdown": float(args.playback_slowdown),
            "duration_sec": float(args.duration_sec),
            "resolution": [int(args.resolution[0]), int(args.resolution[1])],
            "samples": int(args.samples),
            "device": str(args.device),
            # A real 24 fps camera has its shutter open for about half a frame.
            # It is worth having here for the same reason a real one would blur:
            # the marble covers 26 mm per frame against its own 40 mm diameter,
            # so without it the roll reads as a series of stationary marbles.
            "motion_blur": True,
            "motion_blur_shutter": 0.5,
        },
        # Hue rotates the marble model's own swirl texture rather than replacing
        # it: 0.5 leaves it as authored. The swirl is load-bearing -- a plain
        # sphere is rotationally invariant on screen, and the swirl is the only
        # thing that shows the marble crosses the step still rolling.
        "balls": {
            "a": {"hue": 0.62, "saturation": 1.15},
            "b": {"hue": 0.95, "saturation": 1.30},
        },
        "camera": {
            "location": list(CAMERA_LOCATION),
            "target": list(CAMERA_TARGET),
            "lens_mm": CAMERA_LENS_MM,
        },
        # In the scenario rather than hard-coded because the flat ships no
        # lights at all and this corner of it had to be built from nothing. The
        # living room is further from the window than the kitchen is, so it
        # needs more of its own light than table_marble_collision did.
        "lighting": {
            "ceiling_panel_strength": 12.0,
            "world_strength": 0.9,
            "sun_energy": 2.2,
            "window_bounce_power": 60.0,
            "table_fill_power": 18.0,
        },
        # Tuned in simulate_nesting_table_step.py; see its module comment for
        # what each one is for. Masses are left out on purpose: they follow from
        # the radii at glass density.
        "physics": {
            "launch_x": -0.480,
            "launch_y": 0.220,
            "launch_speed": 0.98,
            "launch_heading_deg": 0.0,
            "ball_a_radius": BALL_A_DIAMETER / 2.0,
            "ball_b_radius": BALL_B_DIAMETER / 2.0,
            "ball_b_x": 0.190,
            "ball_b_y": 0.212,
            "ball_a_restitution": 0.87,
            "ball_b_restitution": 0.87,
            "ball_a_friction": 0.30,
            "ball_b_friction": 0.30,
            "table_friction": 0.42,
            "table_restitution": 0.34,
            "table_rolling_friction": 0.0060,
            "table_spinning_friction": 0.006,
            "gravity_z": -9.8,
        },
    }
    if args.scenario_overrides_json is not None:
        scenario = deep_merge(
            scenario, json.loads(args.scenario_overrides_json.read_text(encoding="utf-8")))
    return scenario


# --- Blender helpers ----------------------------------------------------------

def world_bbox(obj: bpy.types.Object) -> tuple[Vector, Vector]:
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return (Vector((min(c.x for c in corners), min(c.y for c in corners),
                    min(c.z for c in corners))),
            Vector((max(c.x for c in corners), max(c.y for c in corners),
                    max(c.z for c in corners))))


def look_at(obj: bpy.types.Object, target) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def add_area_light(name, location, power, size, target, color=(1.0, 1.0, 1.0)):
    bpy.ops.object.light_add(type="AREA", location=location)
    light = bpy.context.object
    light.name = name
    light.data.energy = float(power)
    light.data.size = float(size)
    light.data.color = color
    light.visible_camera = False
    look_at(light, target)
    return light


def set_input(node, name, value) -> None:
    socket = node.inputs.get(name)
    if socket is not None:
        socket.default_value = value


def enable_gpu() -> None:
    prefs = bpy.context.preferences.addons.get("cycles")
    if prefs is None:
        return
    cprefs = prefs.preferences
    for backend in ("OPTIX", "CUDA"):
        try:
            cprefs.compute_device_type = backend
        except TypeError:
            continue
        cprefs.get_devices()
        devices = [d for d in cprefs.devices if d.type == backend]
        if devices:
            for device in cprefs.devices:
                device.use = device.type == backend
            print(f"[INFO] Cycles device backend: {backend} ({len(devices)} device(s))")
            return
    print("[WARN] No OPTIX/CUDA device found; Cycles will fall back to CPU.")


def ray_down(x: float, y: float, top: float = 0.60):
    """Height of the topmost surface over (x, y) in the scene frame."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    hit, location, _n, _i, obj, _m = bpy.context.scene.ray_cast(
        depsgraph, Vector((x, y, top)), Vector((0.0, 0.0, -1.0)))
    return (location.z, obj) if hit else (None, None)


# --- Room ---------------------------------------------------------------------

def import_room() -> bpy.types.Object:
    """Import the flat and drop it into this scene's world frame.

    Rather than baking a transform into every one of the model's 124 meshes, all
    of its root objects are parented to a single empty carrying the unit
    conversion, the half turn and the shift onto the lip. Parenting with an
    identity parent-inverse means each mesh's own transform composes with the
    empty's, so the model stays internally untouched, the mapping stays
    inspectable in the saved .blend, and -- the point -- the collider export and
    the render read the exact same matrices.
    """
    if not ROOM_GLB.exists():
        raise FileNotFoundError(f"Room model not found: {ROOM_GLB}")
    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(ROOM_GLB))
    imported = [o for o in bpy.context.scene.objects if o not in before]

    # p_room  = S * p_model + t                (the shared calibration)
    # p_scene = Rz(pi) * (p_room - lip)
    #         = Rz(pi) * S * p_model + Rz(pi) * (t - lip)
    t = Vector((-ROOM_SCALE * 419.249, -ROOM_SCALE * 410.898,
                -ROOM_SCALE * ROOM_TABLE_UNITS))
    lip = Vector((ROOM_LIP_X, ROOM_CENTRE_Y, ROOM_HIGH_TOP_Z))
    yaw = Matrix.Rotation(ROOM_TO_SCENE_YAW, 4, "Z")

    room_root = bpy.data.objects.new("room_root", None)
    bpy.context.scene.collection.objects.link(room_root)
    room_root.location = yaw @ (t - lip)
    room_root.rotation_euler = (0.0, 0.0, ROOM_TO_SCENE_YAW)
    room_root.scale = (ROOM_SCALE, ROOM_SCALE, ROOM_SCALE)

    for obj in imported:
        if obj.parent is None:
            obj.parent = room_root
            obj.matrix_parent_inverse = Matrix.Identity(4)

    bpy.context.view_layer.update()
    verify_tops()
    return room_root


def verify_tops() -> None:
    """Re-measure the two tops instead of trusting the constants above.

    Names are no evidence here and neither are bounding boxes: both tops belong
    to one mesh called "Material2.044", so the only way to tell the taller table
    from the shorter one -- or to notice that the model has been re-exported
    under us -- is to raycast the surfaces and look at what comes back.
    """
    problems = []

    for label, xs, ys, want in (("taller", HIGH_X, HIGH_Y, 0.0),
                                ("shorter", LOW_X, LOW_Y, LOW_TOP_Z)):
        # A probe that comes back *above* the surface has landed on the
        # dressing, not on the table -- a candle, the vase, the books -- so it
        # says nothing about where the top is. Only probes at or below the
        # expected height are evidence, and the check is that enough of them
        # agree: if the top has genuinely moved, all of them move together.
        on_top, dressed, misses = [], 0, 0
        for i in range(9):
            for j in range(9):
                x = xs[0] + (xs[1] - xs[0]) * (i + 0.5) / 9.0
                y = ys[0] + (ys[1] - ys[0]) * (j + 0.5) / 9.0
                z, _obj = ray_down(x, y)
                if z is None:
                    misses += 1
                elif z > want + VERIFY_TOLERANCE:
                    dressed += 1
                elif z > want - 0.05:
                    on_top.append(z)
        if len(on_top) < 40:
            problems.append(f"only {len(on_top)} of 81 probes over the {label} "
                            f"top came back at z = {want:+.4f}")
            continue
        drift = max(abs(z - want) for z in on_top)
        if drift > VERIFY_TOLERANCE:
            problems.append(f"the {label} top is {drift * 1000:.1f} mm off "
                            f"z = {want:+.4f}")
        print(f"[INFO] {label.capitalize()} top: {len(on_top)} of 81 probes at "
              f"z = {min(on_top):+.4f}..{max(on_top):+.4f} (want {want:+.4f}); "
              f"{dressed} landed on the dressing, {misses} on nothing.")

    # The lip itself: walk in from either side along the lane the marbles use.
    lane = 0.22
    lip = None
    x = -0.05
    while x <= 0.05:
        z, _obj = ray_down(x, lane)
        if z is not None and abs(z - LOW_TOP_Z) < VERIFY_TOLERANCE and lip is None:
            lip = x
            break
        x += 0.0005
    if lip is None:
        problems.append("the step between the two tops is not at x = 0")
    else:
        print(f"[INFO] Step: the shorter top starts at x = {lip:+.4f}, "
              f"{STEP_HEIGHT * 1000:.1f} mm below the taller one.")
        if abs(lip) > VERIFY_TOLERANCE:
            problems.append(f"the step is at x = {lip:+.4f}, not 0")

    for problem in problems:
        print(f"[WARN] {problem}. The calibration in this file no longer "
              "describes the model; every constant in it needs re-deriving.")


def light_the_ceiling_panel(strength: float) -> None:
    """Turn the ceiling's luminous panel into an actual light."""
    face = bpy.data.objects.get(CEILING_PANEL_FACE)
    if face is None or not face.data.materials:
        print(f"[WARN] {CEILING_PANEL_FACE!r} (luminous ceiling panel) is not in "
              "the model or carries no material; the room will be lit only by "
              "the window.")
        return
    material = face.data.materials[0].copy()
    material.name = "ceiling_panel_emission"
    face.data.materials[0] = material
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    for node in list(nodes):
        nodes.remove(node)
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (*CEILING_PANEL_COLOR, 1.0)
    emission.inputs["Strength"].default_value = float(strength)
    links.new(emission.outputs["Emission"], output.inputs["Surface"])

    diffuser = bpy.data.objects.get(CEILING_PANEL_DIFFUSER)
    if diffuser is not None:
        diffuser.hide_render = True
        diffuser.hide_viewport = True
    print(f"[INFO] Luminous ceiling panel lit at strength {strength}; "
          f"diffuser {'hidden' if diffuser is not None else 'not found'}.")


def polish_tops() -> None:
    """Give the two tops the sheen a lacquered wooden top has.

    The object is found by raycasting rather than by name -- whatever mesh the
    taller top's surface belongs to is the one to polish, and in this model that
    happens to be the same mesh as the shorter top, so one copy covers both.
    """
    _z, obj = ray_down((HIGH_X[0] + HIGH_X[1]) / 2.0, 0.22)
    if obj is None or not obj.data.materials:
        print("[WARN] The nesting tops carry no material to polish.")
        return
    material = obj.data.materials[0].copy()
    material.name = "nesting_top"
    obj.data.materials[0] = material
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        print(f"[WARN] {obj.name!r} has no Principled BSDF to polish.")
        return
    set_input(bsdf, "Roughness", 0.24)
    set_input(bsdf, "Specular", 0.55)
    print(f"[INFO] Polished the nesting tops ({obj.name}): roughness 0.24.")


# --- Marbles ------------------------------------------------------------------

def import_marble(name: str, diameter: float, hue: float, saturation: float):
    """Import the marble model, size it, centre it, and recolour it.

    The model ships as one sphere carrying a photographed swirl on an image
    texture, and the colour is changed by rotating the hue of that texture
    rather than by overwriting the base colour, which would throw the swirl away
    along with the yellow. Each marble gets its own copy of the material,
    otherwise both would share one datablock and recolouring the second would
    recolour the first.
    """
    if not MARBLE_GLB.exists():
        raise FileNotFoundError(f"Marble model not found: {MARBLE_GLB}")

    scene = bpy.context.scene
    before = set(scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(MARBLE_GLB))
    imported = [o for o in scene.objects if o not in before]
    meshes = [o for o in imported if o.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"{MARBLE_GLB.name} imported no mesh.")

    ball = meshes[0]
    ball.name = name
    matrix = ball.matrix_world.copy()
    ball.parent = None
    ball.matrix_world = matrix
    for obj in imported:
        if obj is not ball:
            bpy.data.objects.remove(obj, do_unlink=True)

    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = ball
    ball.select_set(True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # Scale to the physical diameter, then put the origin at the sphere's
    # centre. The simulation hands over centre positions and spin quaternions,
    # so the object's origin has to *be* the centre or the marble visibly orbits
    # its own offset as it rolls -- and on a 47.6 mm step that offset would be a
    # large fraction of the whole event.
    mn, mx = world_bbox(ball)
    span = max(mx.z - mn.z, 1e-9)
    ball.scale = (float(diameter) / span,) * 3
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
    ball.location = (0.0, 0.0, 0.0)
    ball.select_set(False)

    material = ball.data.materials[0].copy() if ball.data.materials else None
    if material is None:
        raise RuntimeError(f"{MARBLE_GLB.name} carries no material to recolour.")
    material.name = f"{name}_glass"
    ball.data.materials[0] = material
    nodes, links = material.node_tree.nodes, material.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    base = bsdf.inputs.get("Base Color") if bsdf is not None else None
    if base is not None and base.is_linked:
        source = base.links[0].from_socket
        hue_sat = nodes.new("ShaderNodeHueSaturation")
        hue_sat.inputs["Hue"].default_value = float(hue)
        hue_sat.inputs["Saturation"].default_value = float(saturation)
        links.new(source, hue_sat.inputs["Color"])
        links.new(hue_sat.outputs["Color"], base)
    else:
        print(f"[WARN] {name}: base colour is not textured; hue shift skipped.")

    for poly in ball.data.polygons:
        poly.use_smooth = True
    ball.rotation_mode = "QUATERNION"
    print(f"[INFO] Marble {name}: {span:.4f} model units -> {diameter:.3f} m, hue {hue}")
    return ball


# --- Colliders ----------------------------------------------------------------

def export_props_collider(path: Path) -> Path:
    """Write everything standing on the two tops as a metre-scale OBJ.

    Selected by *region*, not by name. Every mesh whose bounding box reaches
    into the box over the two tops is duplicated and joined, and then every
    vertex that is not strictly above whichever top it stands over is deleted.
    What survives is the candles, the marble cylinder and the tall vase on the
    shorter table and the stack of books on the taller one -- and nothing else,
    including the tops themselves, which the simulation models as boxes and
    which would otherwise be present twice.

    Doing it geometrically rather than from a list of object names is what makes
    this safe in a model where the names carry no information: a re-export that
    renumbers every mesh changes nothing here.

    Axis conversion is switched off (forward +Y, up +Z). Blender's OBJ exporter
    defaults to the Y-up convention and PyBullet reads OBJ coordinates literally
    into its own Z-up world, so the default would lay the whole thing on its
    side.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    region_min = Vector((PROPS_REGION_X[0], PROPS_REGION_Y[0], PROPS_REGION_Z[0]))
    region_max = Vector((PROPS_REGION_X[1], PROPS_REGION_Y[1], PROPS_REGION_Z[1]))

    candidates = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        mn, mx = world_bbox(obj)
        if all(mn[i] <= region_max[i] and mx[i] >= region_min[i] for i in range(3)):
            candidates.append(obj)
    if not candidates:
        raise RuntimeError("No geometry found over the two nesting tops; the "
                           "room model or the calibration has changed.")

    bpy.ops.object.select_all(action="DESELECT")
    for obj in candidates:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = candidates[0]
    bpy.ops.object.duplicate()
    dupes = [o for o in bpy.context.selected_objects]
    bpy.context.view_layer.objects.active = dupes[0]
    bpy.ops.object.join()
    merged = bpy.context.object
    merged.name = "nesting_tops_dressing"

    mesh = merged.data
    matrix = merged.matrix_world
    doomed = []
    for vert in mesh.vertices:
        world = matrix @ vert.co
        if not all(region_min[i] <= world[i] <= region_max[i] for i in range(3)):
            doomed.append(vert.index)
            continue
        floor = LOW_TOP_Z if world.x > 0.0 else 0.0
        if world.z <= floor + PROPS_CLEARANCE:
            doomed.append(vert.index)
    kept = len(mesh.vertices) - len(doomed)
    if kept == 0:
        raise RuntimeError("Nothing is standing on either top; expected the "
                           "candles, the vase and the stack of books.")

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    for index in doomed:
        mesh.vertices[index].select = True
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.delete(type="VERT")
    bpy.ops.object.mode_set(mode="OBJECT")

    mn, mx = world_bbox(merged)
    print(f"[INFO] Table-top dressing: {len(candidates)} source meshes, "
          f"{kept} vertices kept of {kept + len(doomed)}, "
          f"x {mn.x:+.3f}..{mx.x:+.3f} y {mn.y:+.3f}..{mx.y:+.3f} "
          f"z {mn.z:+.3f}..{mx.z:+.3f}")

    bpy.ops.object.select_all(action="DESELECT")
    merged.select_set(True)
    bpy.ops.wm.obj_export(
        filepath=str(path),
        export_selected_objects=True,
        export_triangulated_mesh=True,
        export_materials=False,
        export_normals=False,
        export_uv=False,
        export_colors=False,
        apply_modifiers=True,
        forward_axis="Y",
        up_axis="Z",
    )
    bpy.data.objects.remove(merged, do_unlink=True)
    print(f"[INFO] Wrote table-top dressing collision mesh: {path}")
    return path


# --- Physics ------------------------------------------------------------------

def apply_keyframes(obj: bpy.types.Object, frames: list, key: str) -> None:
    obj.rotation_mode = "QUATERNION"
    for fr in frames:
        d = fr[key]
        obj.location = d["location"]
        q = d["quaternion_xyzw"]
        obj.rotation_quaternion = (q[3], q[0], q[1], q[2])
        obj.keyframe_insert(data_path="location", frame=int(fr["frame_index"]))
        obj.keyframe_insert(data_path="rotation_quaternion", frame=int(fr["frame_index"]))
    if obj.animation_data and obj.animation_data.action:
        for fc in obj.animation_data.action.fcurves:
            for k in fc.keyframe_points:
                k.interpolation = "LINEAR"


def run_physics(args: argparse.Namespace, scenario: dict) -> dict:
    physics_python = WORKSPACE_DIR.parent / "miniconda" / "envs" / "physics" / "bin" / "python"
    python = str(physics_python) if physics_python.exists() else (
        shutil.which("python3") or shutil.which("python"))
    if not python:
        raise RuntimeError("Cannot find python3/python for the PyBullet simulation.")

    ph = scenario["physics"]
    render = scenario["render"]
    out = args.out_dir / PHYSICS_TEMP_NAME
    command = [
        python, str(Path(__file__).with_name("simulate_nesting_table_step.py")),
        "--out", str(out),
        "--props-collider", str(PROPS_COLLIDER_OBJ),
        "--fps", str(int(render["fps"])),
        "--playback-slowdown", str(float(render["playback_slowdown"])),
        "--duration-sec", str(float(render["duration_sec"])),
    ]
    for flag, key in (
        ("--ball-a-radius", "ball_a_radius"),
        ("--ball-b-radius", "ball_b_radius"),
        ("--launch-x", "launch_x"),
        ("--launch-y", "launch_y"),
        ("--launch-speed", "launch_speed"),
        ("--launch-heading-deg", "launch_heading_deg"),
        ("--ball-b-x", "ball_b_x"),
        ("--ball-b-y", "ball_b_y"),
        ("--ball-a-restitution", "ball_a_restitution"),
        ("--ball-b-restitution", "ball_b_restitution"),
        ("--ball-a-friction", "ball_a_friction"),
        ("--ball-b-friction", "ball_b_friction"),
        ("--table-friction", "table_friction"),
        ("--table-restitution", "table_restitution"),
        ("--table-rolling-friction", "table_rolling_friction"),
        ("--table-spinning-friction", "table_spinning_friction"),
        ("--gravity-z", "gravity_z"),
    ):
        command += [flag, str(float(ph[key]))]
    for flag, key in (("--ball-a-mass", "ball_a_mass"), ("--ball-b-mass", "ball_b_mass")):
        if ph.get(key) is not None:
            command += [flag, str(float(ph[key]))]

    subprocess.run(command, check=True)
    data = json.loads(out.read_text(encoding="utf-8"))
    out.unlink(missing_ok=True)

    q, s, c = data["quality"], data["step"], data["collision"]
    if not q["made_the_step"]:
        print("[WARN] The marble never landed on the shorter table in this scenario.")
    if not q["hit_ball_b"]:
        print("[WARN] The marble never reached the second one.")
    if q["still_bouncing_at_impact"]:
        print("[WARN] The impact happens within a frame of the landing: the "
              "marble is still coming down when it hits, which is not the "
              "collision the closed form describes. Move --ball-b-x out.")
    if q["spinning_on_the_spot"]:
        print(f"[WARN] A marble is still turning where it stopped: "
              f"{q['residual_spin']} rad/s. Check table_spinning_friction.")
    if q["hit_table_dressing"]:
        print(f"[WARN] A marble reached the candles, the vase or the books at "
              f"frame {q['prop_contact_frame']}; the shot passes clear of them.")
    if q["left_tables"] is not None:
        print(f"[WARN] {q['left_tables']['ball']} left the tables at frame "
              f"{q['left_tables']['frame']} over the {q['left_tables']['edge']} edge.")
    if q["ball_a_settled_frame"] is None or q["ball_b_settled_frame"] is None:
        print("[WARN] A marble was still moving on the last frame; it never "
              "settles on camera.")

    # The scene's standing checks: the solver against the two closed forms. A
    # quiet disagreement here is the single most likely way for this scene to be
    # wrong, so both are compared on every render rather than once during
    # tuning.
    if s["measured"] is not None and s["predicted"] is not None:
        # The tolerances differ on purpose. The vertical speed on landing and
        # the horizontal speed across the step are exact statements about a
        # body in free fall and are held to a couple of per cent. The flight
        # time and the range depend on where the marble is judged to have left
        # the lip, which is a threshold on a contact, so they get more room.
        for label, key, tol in (("flight time", "flight_time", 0.06),
                                ("range from the lip", "range_from_lip", 0.08),
                                ("landing vertical speed", "landing_vertical_speed", 0.03),
                                ("horizontal speed across the step",
                                 "horizontal_speed_ratio", 0.02)):
            measured, predicted = s["measured"][key], s["predicted"][key]
            if measured is None or predicted is None:
                continue
            if abs(measured - predicted) > tol * abs(predicted) + 1e-4:
                print(f"[WARN] The {label} came out at {measured:.4f}, but the "
                      f"closed form for a {s['height'] * 1000:.1f} mm step "
                      f"predicts {predicted:.4f}. One of them is wrong.")
        print("[INFO] Off a %.1f mm step at %.3f m/s: flight %.4f s (predicted "
              "%.4f), %.3f m from the lip (predicted %.3f), landing at %.3f m/s "
              "down (predicted %.3f). Horizontal speed across the step: %.4f of "
              "what it was; spin: %.4f." % (
                  s["height"] * 1000,
                  math.hypot(s["lip_velocity"][0], s["lip_velocity"][1]),
                  s["measured"]["flight_time"], s["predicted"]["flight_time"],
                  s["measured"]["range_from_lip"], s["predicted"]["range_from_lip"],
                  s["measured"]["landing_vertical_speed"],
                  s["predicted"]["landing_vertical_speed"],
                  s["measured"]["horizontal_speed_ratio"],
                  s["measured"]["spin_ratio"] or float("nan")))
    for label, measured, predicted in (
        ("struck marble", c["measured_b_speed_ratio"], c["predicted"]["b_speed_ratio"]),
        ("roller", c["measured_a_speed_ratio"], c["predicted"]["a_speed_ratio"]),
    ):
        if measured is None:
            continue
        # The floor is not slack: when the closed form predicts the roller
        # nearly stopping, what it has left is mostly what friction and spin did
        # during the contact, which no rigid-impact formula models.
        if abs(measured - predicted) > 0.15 * predicted + 0.02:
            print(f"[WARN] The {label} left at {measured:.3f} x the approach "
                  f"speed, but the closed form for a {c['mass_ratio']:.2f}:1 "
                  f"impact at e = {c['effective_restitution']:.2f} and "
                  f"{c['obliquity_deg']:.1f} deg predicts {predicted:.3f}. "
                  "One of them is wrong.")
    return data


# --- Scene --------------------------------------------------------------------

def build_scene(args: argparse.Namespace, scenario: dict, physics: dict):
    scene = bpy.context.scene
    render = scenario["render"]
    scene.render.resolution_x = int(render["resolution"][0])
    scene.render.resolution_y = int(render["resolution"][1])
    scene.render.fps = int(render["fps"])
    scene.render.engine = "CYCLES"
    scene.cycles.samples = int(render["samples"])
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 12
    scene.cycles.transmission_bounces = 12
    scene.render.use_motion_blur = bool(render.get("motion_blur", True))
    scene.render.motion_blur_shutter = float(render.get("motion_blur_shutter", 0.18))
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "MEDIUM"
    scene.view_settings.look = "Medium Contrast"

    if str(render["device"]) == "auto":
        enable_gpu()
        scene.cycles.device = "GPU"
    else:
        scene.cycles.device = "CPU"

    # World: a sky, not a room. The flat has one large glazed wall, and what
    # that window sees is outdoors -- an interior HDRI behind it reads as a
    # second room hanging in the air. Kept dim: the daylight comes from the sun
    # below and the room's own light from the ceiling panel, and this is here to
    # fill the window and give the marbles something to reflect.
    world = bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    for node in list(nodes):
        nodes.remove(node)
    lighting = scenario.get("lighting", {})
    output = nodes.new("ShaderNodeOutputWorld")
    bg = nodes.new("ShaderNodeBackground")
    hdri = POLYHAVEN_DIR / "outdoor" / "kloofendal_48d_partly_cloudy_puresky_2k.hdr"
    if hdri.exists():
        tex_coord = nodes.new("ShaderNodeTexCoord")
        mapping = nodes.new("ShaderNodeMapping")
        # The room turned a half turn when it came into this frame, so the sky
        # has to turn with it or the daylight arrives through a wall.
        mapping.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(95.0 + 180.0))
        env = nodes.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(str(hdri), check_existing=True)
        links.new(tex_coord.outputs["Generated"], mapping.inputs["Vector"])
        links.new(mapping.outputs["Vector"], env.inputs["Vector"])
        links.new(env.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = float(lighting.get("world_strength", 0.9))
    else:
        print(f"[WARN] HDRI not found at {hdri}; falling back to a flat sky colour.")
        bg.inputs["Color"].default_value = (0.62, 0.70, 0.82, 1.0)
        bg.inputs["Strength"].default_value = float(lighting.get("world_strength", 0.9)) * 1.8
    links.new(bg.outputs["Background"], output.inputs["Surface"])

    import_room()
    light_the_ceiling_panel(float(lighting.get("ceiling_panel_strength", 12.0)))
    polish_tops()

    # Sized from what the simulation actually used, not from the scenario, so
    # the two cannot disagree.
    cfg = scenario["balls"]
    ball_a = import_marble("marble_roller",
                           2.0 * float(physics["objects"]["ball_a"]["radius"]),
                           float(cfg["a"]["hue"]), float(cfg["a"]["saturation"]))
    ball_b = import_marble("marble_target",
                           2.0 * float(physics["objects"]["ball_b"]["radius"]),
                           float(cfg["b"]["hue"]), float(cfg["b"]["saturation"]))
    apply_keyframes(ball_a, physics["frames"], "ball_a")
    apply_keyframes(ball_b, physics["frames"], "ball_b")

    cam_cfg = scenario["camera"]
    bpy.ops.object.camera_add(location=tuple(cam_cfg["location"]))
    camera = bpy.context.object
    camera.data.lens = float(cam_cfg["lens_mm"])
    camera.data.sensor_width = 36.0
    camera.data.clip_start = 0.01
    scene.camera = camera
    target = bpy.data.objects.new("camera_target", None)
    scene.collection.objects.link(target)
    target.location = tuple(cam_cfg["target"])
    con = camera.constraints.new("TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"

    # Daylight through the glazed wall, which in this frame is out beyond +x --
    # the same direction the marble travels, so the light rakes along the run
    # rather than flooding it flat. It is what puts a highlight on the marbles
    # and, more importantly, a contact shadow under them: the ceiling panel
    # alone is a big soft box directly overhead and leaves a marble sitting in
    # its own shade with nothing to say whether it is touching the table or
    # 40 mm above it, which on this scene is the whole question.
    bpy.ops.object.light_add(type="SUN", location=(5.46, 0.00, 2.04))
    sun = bpy.context.object
    sun.data.energy = float(lighting.get("sun_energy", 2.2))
    sun.data.angle = math.radians(2.8)
    sun.data.color = (1.0, 0.955, 0.90)
    sun.rotation_euler = (math.radians(66.0), 0.0, math.radians(76.0))

    add_area_light("window_bounce", (2.60, -0.10, 0.55),
                   power=float(lighting.get("window_bounce_power", 60.0)), size=1.8,
                   target=(0.02, 0.10, -0.02), color=(0.94, 0.965, 1.0))
    # A small light on the open side, at the height of someone sitting on the
    # sofa. Without it the marbles' camera-facing halves fall away into the dark
    # inlay, because everything else in the room lights them from behind.
    add_area_light("table_fill", (0.05, 0.85, 0.34),
                   power=float(lighting.get("table_fill_power", 18.0)), size=0.55,
                   target=(0.02, 0.10, -0.01), color=(1.0, 0.98, 0.95))

    scene.frame_start = 1
    scene.frame_end = int(physics["frame_end"])
    scene.frame_set(1)
    return ball_a, ball_b, camera


# --- Outputs ------------------------------------------------------------------

def export_ground_truth(out_dir: Path, ball_a, ball_b, camera, physics: dict,
                        scenario: dict) -> None:
    scene = bpy.context.scene
    fps = int(physics["fps"])
    frame_end = int(physics["frame_end"])
    records = {
        "schema_version": 1,
        "scene": OUTPUT_STEM,
        "fps": fps,
        "playback_slowdown": physics["playback_slowdown"],
        "sample_rate_hz": physics["sample_rate_hz"],
        "frame_start": 1,
        "frame_end": frame_end,
        "physics": {k: v for k, v in physics.items() if k != "frames"},
        "objects": {
            "ball_a": {"object_name": ball_a.name,
                       "radius": physics["objects"]["ball_a"]["radius"],
                       "mass": physics["objects"]["ball_a"]["mass"]},
            "ball_b": {"object_name": ball_b.name,
                       "radius": physics["objects"]["ball_b"]["radius"],
                       "mass": physics["objects"]["ball_b"]["mass"]},
            "high_top": {"footprint_x": list(HIGH_X), "footprint_y": list(HIGH_Y),
                         "top_z": 0.0},
            "low_top": {"footprint_x": list(LOW_X), "footprint_y": list(LOW_Y),
                        "top_z": LOW_TOP_Z},
            "step_height": STEP_HEIGHT,
        },
        "room": {
            "model": ROOM_GLB.name,
            "scale": ROOM_SCALE,
            "room_lip_x": ROOM_LIP_X,
            "room_centre_y": ROOM_CENTRE_Y,
            "room_high_top_z": ROOM_HIGH_TOP_Z,
            "room_to_scene_yaw_rad": ROOM_TO_SCENE_YAW,
            "bar_table_units": ROOM_TABLE_UNITS,
        },
        "camera": {
            "object_name": camera.name,
            "lens_mm": float(camera.data.lens),
            "sensor_width_mm": float(camera.data.sensor_width),
            "resolution": [int(scene.render.resolution_x), int(scene.render.resolution_y)],
        },
        "scenario": scenario,
        "frames": [],
    }
    by_frame = {int(fr["frame_index"]): fr for fr in physics["frames"]}
    for frame in range(1, frame_end + 1):
        scene.frame_set(frame)
        pf = by_frame[frame]
        entry = {"frame_index": frame,
                 "time_sec": pf["time_sec"],
                 "video_time_sec": (frame - 1) / float(fps),
                 "camera_matrix_world": [[float(v) for v in row]
                                         for row in camera.matrix_world]}
        for key, obj in (("ball_a", ball_a), ("ball_b", ball_b)):
            entry[key] = {
                "matrix_world": [[float(v) for v in row] for row in obj.matrix_world],
                "linear_velocity": pf[key]["linear_velocity"],
                "angular_velocity": pf[key]["angular_velocity"],
                "speed": pf[key]["speed"],
                "horizontal_speed": pf[key]["horizontal_speed"],
                "spin_z": pf[key]["spin_z"],
                "surface": pf[key]["surface"],
                "on_table": pf[key]["on_table"],
            }
        entry["ball_a"]["phase"] = pf["ball_a"]["phase"]
        entry["ball_b"]["moving"] = pf["ball_b"]["moving"]
        records["frames"].append(entry)
    (out_dir / GROUND_TRUTH_NAME).write_text(json.dumps(records, indent=2), encoding="utf-8")


def render_preview(args: argparse.Namespace) -> None:
    scene = bpy.context.scene
    scene.render.image_settings.file_format = "PNG"
    if args.preview_frames:
        for frame in args.preview_frames:
            scene.frame_set(max(scene.frame_start, min(int(frame), scene.frame_end)))
            scene.render.filepath = str(args.out_dir / f"preview_f{int(frame):03d}.png")
            bpy.ops.render.render(write_still=True)
        return
    scene.frame_set(max(scene.frame_start, min(int(args.preview_frame), scene.frame_end)))
    scene.render.filepath = str(args.out_dir / "preview.png")
    bpy.ops.render.render(write_still=True)


def render_frames(args: argparse.Namespace) -> None:
    scene = bpy.context.scene
    scene.render.image_settings.file_format = "PNG"
    scene.frame_set(scene.frame_start)
    scene.render.filepath = str(args.out_dir / "frame_")
    bpy.ops.render.render(animation=True)


def render_animation(args: argparse.Namespace) -> None:
    scene = bpy.context.scene
    scene.frame_set(scene.frame_start)
    scene.render.filepath = str(args.out_dir / f"{OUTPUT_STEM}.mp4")
    bpy.ops.render.render(animation=True)


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    stale = not PROPS_COLLIDER_OBJ.exists()
    if args.mode == "colliders" or args.refresh_colliders or stale:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        import_room()
        export_props_collider(PROPS_COLLIDER_OBJ)
        if args.mode == "colliders":
            return

    scenario = build_scenario(args)
    (args.out_dir / SCENARIO_METADATA_NAME).write_text(
        json.dumps(scenario, indent=2), encoding="utf-8")

    physics = run_physics(args, scenario)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ball_a, ball_b, camera = build_scene(args, scenario, physics)
    export_ground_truth(args.out_dir, ball_a, ball_b, camera, physics, scenario)

    if args.mode == "preview":
        render_preview(args)
    elif args.mode == "frames":
        render_frames(args)
    else:
        render_animation(args)

    bpy.ops.wm.save_as_mainfile(filepath=str(args.out_dir / BLEND_NAME))
    print(f"[INFO] Render complete. Output: {args.out_dir}")


if __name__ == "__main__":
    main()
