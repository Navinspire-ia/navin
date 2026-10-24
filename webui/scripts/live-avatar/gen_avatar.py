"""Generate a TalkingHead-ready Live avatar with MPFB (CC0 MakeHuman assets).

Run: blender -b --python gen_avatar.py -- <out.glb> <config.json>
Follows TalkingHead's blender/MPFB/MPFB.md, headless. See README.md here.
"""
import importlib.util
import json
import os
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
OUT, CONFIG = argv[0], json.load(open(argv[1]))

from bl_ext.user_default.mpfb.services import (  # noqa: E402
    ExportService,
    FaceService,
    HumanService,
    ObjectService,
    TargetService,
)

ADDON = os.path.expanduser(os.environ.get("TALKINGHEAD_ADDON", "~/tools/th-mpfb/talkinghead-addon.py"))
spec = importlib.util.spec_from_file_location("talkinghead_addon", ADDON)
th = importlib.util.module_from_spec(spec)
spec.loader.exec_module(th)


def log(*args):
    print("GEN:", *args, flush=True)


# -- 1. Clean scene ----------------------------------------------------------
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

# -- 2. Design character -------------------------------------------------------
info = HumanService._create_default_human_info_dict()
info["phenotype"].update(CONFIG["phenotype"])
info["phenotype"]["race"] = CONFIG["race"]
info.update({
    "name": "design",
    "rig": "custom.talkinghead",
    "eyes": "high-poly.mhclo",
    "eyebrows": CONFIG["eyebrows"],
    "eyelashes": CONFIG["eyelashes"],
    "tongue": "tongue01.mhclo",
    "teeth": "teeth_base.mhclo",
    "hair": CONFIG["hair"],
    "clothes": CONFIG["clothes"],
    "skin_mhmat": CONFIG["skin"],
    "skin_material_type": "GAMEENGINE",
    "eyes_material_type": "GAMEENGINE",
    "clothes_material_type": "GAMEENGINE",
    "alternative_materials": {},
    "eyes_material_settings": {},
})
settings = HumanService.get_default_deserialization_settings()
settings["subdiv_levels"] = 0
basemesh = HumanService.deserialize_from_dict(info, settings)
design_rig = basemesh.parent
log("design", basemesh.name, design_rig.name if design_rig else None)

# -- 3. Export copy: visemes + ARKit, baked, no helpers ------------------------
collection = bpy.data.collections.new("export copy")
bpy.context.scene.collection.children.link(collection)
export_copy = ExportService.create_character_copy(basemesh, name_suffix="_export", place_in_collection=collection)
new_basemesh = ObjectService.find_object_of_type_amongst_nearest_relatives(export_copy)
TargetService.bake_targets(new_basemesh)
FaceService.load_targets(new_basemesh, load_microsoft_visemes=False, load_meta_visemes=True, load_arkit_faceunits=True)
FaceService.interpolate_targets(new_basemesh)
ExportService.bake_modifiers_remove_helpers(new_basemesh, bake_masks=True, bake_subdiv=True, remove_helpers=True, also_proxy=True)
log("export copy", export_copy.name, export_copy.type, "shape keys:", len(new_basemesh.data.shape_keys.key_blocks) if new_basemesh.data.shape_keys else 0)

# -- 4. Drop the design, keep the export copy ------------------------------------
design_objects = [o for o in bpy.data.objects if o.users_collection and collection not in o.users_collection]
for obj in design_objects:
    bpy.data.objects.remove(obj, do_unlink=True)
export_copy.name = "Armature"
export_copy.data.name = "Armature"

# -- 5. TalkingHead fixes: scale, bone axes, apply transforms -------------------
bpy.ops.object.select_all(action="DESELECT")
export_copy.select_set(True)
bpy.context.view_layer.objects.active = export_copy
th.scale_character([export_copy])
th.fix_bone_axes([export_copy], th.BONE_AXES_DATA_A)
bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

# -- 6. Materials for glTF: alpha clip where needed (as the TalkingHead MPFB
# example avatar), everything else opaque.
def cutoff_for(name):
    name = name.lower()
    if "high-poly" in name or "low-poly" in name:
        return 0.5   # eyes: the cornea layer must not hide the iris
    if "eyebrow" in name or "eyelash" in name:
        return 0.15  # thin strands: a high threshold erases them
    if any(word in name for word in ("hair", "short", "long", "ponytail", "bob", "braid", "afro")):
        return 0.3
    return None


for mat in bpy.data.materials:
    if not mat.use_nodes:
        continue
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None or not bsdf.inputs["Alpha"].links:
        continue
    alpha_in = bsdf.inputs["Alpha"]
    source = alpha_in.links[0].from_socket
    for link in list(alpha_in.links):
        links.remove(link)
    cutoff = cutoff_for(mat.name)
    if cutoff is not None:
        node = nodes.new("ShaderNodeMath")
        node.operation = "GREATER_THAN"
        node.inputs[1].default_value = cutoff
        links.new(source, node.inputs[0])
        links.new(node.outputs[0], alpha_in)
    log("material", mat.name, "MASK %.2f" % cutoff if cutoff is not None else "OPAQUE")

# -- 7. Export GLB ------------------------------------------------------------
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(
    filepath=OUT,
    export_format="GLB",
    use_selection=True,
    export_animations=False,
    export_morph=True,
    export_skins=True,
    export_apply=False,
)
log("exported", OUT, os.path.getsize(OUT))
