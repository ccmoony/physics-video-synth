from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

WORKSPACE_DIR = Path(__file__).resolve().parents[2]
MODELS_DIR = WORKSPACE_DIR / "assets" / "models"

ROOM_GLB = MODELS_DIR / "dining_room__kichen_baked.glb"
CAN_GLB = MODELS_DIR / "simple_cola_can.glb"
CUP_GLB = MODELS_DIR / "fast_food_soda_cup.glb"
MILK_GLB = MODELS_DIR / "milk_packaging.glb"

OUTPUT_STEM = "dining_chain"
PHYSICS_TEMP = "physics_transforms.json"

# Uniform scale factors that bring each raw GLB to its real-world size, and the
# collision-proxy half-height used by simulate_dining_chain.py (the physics
# body centre is placed at base + this height, so the visual origin is set to
# match). Can/cup GLBs come in at ~2-5 blender-units; the milk carton is
# already near metres.
CAN_SCALE = 0.122 / 3.260      # -> 12.2 cm tall
CUP_SCALE = 0.088 / 2.396      # -> ~8.8 cm body diameter (Y, straw-free axis)
MILK_SCALE = 0.125 / 0.297     # -> 12.5 cm tall carton (~can height)

CONTAINERS = {
    # key: (glb, scale, proxy_half_height_m)
    "can": (CAN_GLB, CAN_SCALE, 0.061),
    "cup": (CUP_GLB, CUP_SCALE, 0.080),
    "milk": (MILK_GLB, MILK_SCALE, 0.0625),
}

# Straight-on front view: camera faces the chain along -X, roughly level with
# the drinks, so the three of them stand side-by-side and slide left->right
# (along +Y) across the frame rather than at an oblique angle. The dining
# chairs on the camera (+X) side are culled (see CULL_SEATS_BEYOND_X) so they
# don't block this near-level view; the far chairs stay as background.
CAMERA_LOCATION = (2.90, -0.50, 1.02)
CAMERA_TARGET = (1.15, -0.50, 0.85)
CAMERA_LENS_MM = 35.0

# Delete seat geometry beyond this world-X (past the +X table edge at 1.64):
# removes the chair between camera and table without touching the table, the
# drinks (x=1.15), or the background chairs.
CULL_SEATS_BEYOND_X = 1.5

# Interior lighting (no HDRI): a neutral world fill + a warm key over the table
# + a cool fill from the window (+Y) side.
WORLD_STRENGTH = 0.20
KEY_LIGHT = {"loc": (0.9, -0.5, 1.7), "power": 40.0, "size": 1.1, "color": (1.0, 0.95, 0.86)}
WINDOW_LIGHT = {"loc": (1.4, 2.6, 1.5), "power": 25.0, "size": 2.0, "color": (0.85, 0.9, 1.0)}
# The room's baked textures are very pale/high-albedo, so it reads bright under
# any lighting; a negative color-management exposure (stops) knocks the whole
# image down to a calmer level. -0.8 ~= 0.57x brightness.
EXPOSURE = -0.8
LIGHT_TARGET = (1.05, -0.5, 0.80)


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preview", "animation"), default="animation")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--resolution", nargs=2, type=int, default=(960, 540))
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--duration-sec", type=float, default=3.0)
    parser.add_argument("--samples", type=int, default=96)
    parser.add_argument("--preview-frame", type=int, default=30)
    parser.add_argument("--device", choices=("auto", "cpu"), default="auto")
    parser.add_argument("--launch-speed", type=float, default=3.3)
    parser.add_argument("--table-friction", type=float, default=0.30)
    return parser.parse_args(argv)


def world_bbox(obj: bpy.types.Object) -> tuple[Vector, Vector]:
    cs = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [c.x for c in cs]; ys = [c.y for c in cs]; zs = [c.z for c in cs]
    return Vector((min(xs), min(ys), min(zs))), Vector((max(xs), max(ys), max(zs)))


def look_at(obj: bpy.types.Object, target) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def add_area_light(name, loc, power, size, target, color=(1, 1, 1)) -> None:
    bpy.ops.object.light_add(type="AREA", location=loc)
    light = bpy.context.object
    light.name = name
    light.data.energy = float(power)
    light.data.size = float(size)
    light.data.color = color
    look_at(light, target)


def run_physics(args: argparse.Namespace) -> dict:
    physics_python = WORKSPACE_DIR.parent / "miniconda" / "envs" / "physics" / "bin" / "python"
    python = str(physics_python) if physics_python.exists() else (shutil.which("python3") or shutil.which("python"))
    script = Path(__file__).with_name("simulate_dining_chain.py")
    out = args.out_dir / PHYSICS_TEMP
    subprocess.run([
        python, str(script), "--out", str(out),
        "--fps", str(int(args.fps)), "--duration-sec", str(float(args.duration_sec)),
        "--launch-speed", str(float(args.launch_speed)),
        "--table-friction", str(float(args.table_friction)),
    ], check=True)
    data = json.loads(out.read_text(encoding="utf-8"))
    out.unlink(missing_ok=True)
    return data


def import_container(glb: Path, name: str, scale: float, proxy_half_h: float) -> bpy.types.Object:
    existing = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(glb))
    imported = [o for o in bpy.context.scene.objects if o not in existing]
    meshes = [o for o in imported if o.type == "MESH"]
    # Capture non-mesh (empty) names before joining -- join invalidates the
    # merged mesh object references, so we clean up by name lookup afterwards.
    empty_names = [o.name for o in imported if o.type != "MESH"]

    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if len(meshes) > 1:
        bpy.ops.object.select_all(action="DESELECT")
        for o in meshes:
            o.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    for nm in empty_names:
        e = bpy.data.objects.get(nm)
        if e is not None and e is not obj:
            bpy.data.objects.remove(e, do_unlink=True)

    obj.scale = (scale, scale, scale)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    mn, mx = world_bbox(obj)
    bpy.context.scene.cursor.location = ((mn.x + mx.x) / 2.0, (mn.y + mx.y) / 2.0, mn.z + proxy_half_h)
    bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    obj.name = name
    obj.rotation_mode = "QUATERNION"
    return obj


def cull_seats_beyond_x(x_threshold: float) -> None:
    """Delete seat/chair vertices with world-x beyond the threshold (the chairs
    on the camera side that would block the front view)."""
    for obj in list(bpy.context.scene.objects):
        if obj.type != "MESH" or "seat" not in obj.name.lower():
            continue
        mw = obj.matrix_world
        me = obj.data
        bm = bmesh.new()
        bm.from_mesh(me)
        doomed = [v for v in bm.verts if (mw @ v.co).x > x_threshold]
        if doomed:
            bmesh.ops.delete(bm, geom=doomed, context="VERTS")
        bm.to_mesh(me)
        bm.free()
        me.update()


def apply_keyframes(obj: bpy.types.Object, frames: list, key: str) -> None:
    obj.rotation_mode = "QUATERNION"
    for fr in frames:
        d = fr["objects"][key]
        obj.location = d["location"]
        q = d["quaternion_xyzw"]
        obj.rotation_quaternion = (q[3], q[0], q[1], q[2])
        obj.keyframe_insert(data_path="location", frame=int(fr["frame_index"]))
        obj.keyframe_insert(data_path="rotation_quaternion", frame=int(fr["frame_index"]))
    if obj.animation_data and obj.animation_data.action:
        for fc in obj.animation_data.action.fcurves:
            for k in fc.keyframe_points:
                k.interpolation = "LINEAR"


def build_scene(args: argparse.Namespace, physics: dict) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.resolution_x = args.resolution[0]
    scene.render.resolution_y = args.resolution[1]
    scene.render.fps = args.fps
    scene.render.engine = "CYCLES"
    scene.cycles.samples = args.samples
    scene.cycles.device = "GPU" if args.device == "auto" else "CPU"
    scene.cycles.max_bounces = 12
    scene.cycles.transmission_bounces = 8
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "MEDIUM"
    scene.view_settings.exposure = EXPOSURE

    # World: plain neutral fill, no HDRI.
    world = bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.55, 0.56, 0.58, 1.0)
    bg.inputs["Strength"].default_value = WORLD_STRENGTH

    # Camera
    bpy.ops.object.camera_add(location=CAMERA_LOCATION)
    camera = bpy.context.object
    camera.data.lens = CAMERA_LENS_MM
    scene.camera = camera
    target = bpy.data.objects.new("camera_target", None)
    scene.collection.objects.link(target)
    target.location = CAMERA_TARGET
    con = camera.constraints.new("TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"

    # Interior lights
    add_area_light("key_light", KEY_LIGHT["loc"], KEY_LIGHT["power"], KEY_LIGHT["size"], LIGHT_TARGET, KEY_LIGHT["color"])
    add_area_light("window_light", WINDOW_LIGHT["loc"], WINDOW_LIGHT["power"], WINDOW_LIGHT["size"], LIGHT_TARGET, WINDOW_LIGHT["color"])

    # Static dining room
    if not ROOM_GLB.exists():
        raise FileNotFoundError(f"Room model not found: {ROOM_GLB}")
    bpy.ops.import_scene.gltf(filepath=str(ROOM_GLB))
    cull_seats_beyond_x(CULL_SEATS_BEYOND_X)

    # Chain objects
    frames = physics["frames"]
    for key, (glb, scale, half_h) in CONTAINERS.items():
        obj = import_container(glb, f"chain_{key}", scale, half_h)
        apply_keyframes(obj, frames, key)

    frame_end = int(physics["frame_end"])
    scene.frame_start = 1
    scene.frame_end = frame_end


def render_preview(args: argparse.Namespace) -> None:
    scene = bpy.context.scene
    scene.frame_set(max(1, min(int(args.preview_frame), scene.frame_end)))
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(args.out_dir / "preview.png")
    bpy.ops.render.render(write_still=True)


def render_animation(args: argparse.Namespace) -> None:
    scene = bpy.context.scene
    scene.frame_set(scene.frame_start)
    scene.render.filepath = str(args.out_dir / f"{OUTPUT_STEM}.mp4")
    bpy.ops.render.render(animation=True)


def main() -> None:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    physics = run_physics(args)
    build_scene(args, physics)
    if args.mode == "preview":
        render_preview(args)
    else:
        render_animation(args)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.out_dir / f"{OUTPUT_STEM}.blend"))
    print(f"[INFO] Render complete. Output: {args.out_dir}")


if __name__ == "__main__":
    main()
