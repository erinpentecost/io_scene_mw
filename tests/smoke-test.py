#!/usr/bin/env python3
"""Build io_scene_mw.zip, install/enable it in Blender, and run smoke tests."""

import os
import shutil
import subprocess
import zipfile


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = "io_scene_mw"
ADDON_ROOT = os.path.join(PROJECT_ROOT, ADDON_DIR)
ZIP_FILE = "io_scene_mw.zip"
BLENDER = "blender-5.1"


def build_zip():
    """Create a clean zip of the add-on."""
    zip_path = os.path.join(PROJECT_ROOT, ZIP_FILE)

    if os.path.exists(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(ADDON_ROOT):
            dirs[:] = [
                d for d in dirs
                if d != "__pycache__" and not d.startswith(".")
            ]

            for file in files:
                if file.startswith("."):
                    continue

                file_full_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_full_path, ADDON_ROOT)
                arcname = os.path.join(ADDON_DIR, rel_path)
                zf.write(file_full_path, arcname)

    print(f"Created {zip_path}")


def install_and_enable():
    """Install, enable, and run the three smoke-test cases in Blender."""
    zip_path = os.path.join(PROJECT_ROOT, ZIP_FILE)
    output_dir = os.path.join(PROJECT_ROOT, "tests", "smoke_output")
    os.makedirs(output_dir, exist_ok=True)

    clannfear_nif = os.path.join(
        PROJECT_ROOT, "tests", "tr_clannfear_lesser.nif"
    )
    clannfear_reexport = os.path.join(
        output_dir, "tr_clannfear_lesser_reexport.nif"
    )

    anubis_blend = os.path.join(PROJECT_ROOT, "tests", "anubis.blend")
    anubis_nif = os.path.join(output_dir, "anubis.nif")
    anubis_xnif = os.path.join(output_dir, "xanubis.nif")

    script = f"""
import bpy
import os
import shutil

zip_path = {zip_path!r}
output_dir = {output_dir!r}
clannfear_nif = {clannfear_nif!r}
clannfear_reexport = {clannfear_reexport!r}
anubis_blend = {anubis_blend!r}
anubis_nif = {anubis_nif!r}
anubis_xnif = {anubis_xnif!r}


def clear_scene():
    bpy.ops.object.mode_set(mode='OBJECT') if bpy.context.object and bpy.context.object.mode != 'OBJECT' else None
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

    for collection in list(bpy.data.collections):
        if collection.users == 0:
            bpy.data.collections.remove(collection)

    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)

    for armature in list(bpy.data.armatures):
        if armature.users == 0:
            bpy.data.armatures.remove(armature)

    for action in list(bpy.data.actions):
        if action.users == 0:
            bpy.data.actions.remove(action)


def check_exists(path):
    if not os.path.exists(path):
        raise RuntimeError(f"Expected output missing: {{path}}")
    print(f"OK: {{path}}")


# Install and enable the add-on.
bpy.ops.preferences.addon_install(
    filepath=zip_path,
    overwrite=True,
    enable_on_install=True,
)
bpy.ops.preferences.addon_enable(module='io_scene_mw')
print('io_scene_mw installed and enabled.')


# -------------------------------------------------------------------------
# 1. Import tr_clannfear_lesser.nif and re-export it.
# -------------------------------------------------------------------------
clear_scene()

if not os.path.exists(clannfear_nif):
    raise FileNotFoundError(f"Test input not found: {{clannfear_nif}}")

print('1/3: Importing tr_clannfear_lesser.nif...')
bpy.ops.import_scene.mw(filepath=clannfear_nif)

print('1/3: Re-exporting tr_clannfear_lesser_reexport.nif...')
bpy.ops.export_scene.mw(
    filepath=clannfear_reexport,
    export_animations=True,
    extract_keyframe_data=False,
)
check_exists(clannfear_reexport)


# -------------------------------------------------------------------------
# 2. Load anubis.blend and batch-export animations.
# -------------------------------------------------------------------------
clear_scene()

if not os.path.exists(anubis_blend):
    raise FileNotFoundError(f"Test input not found: {{anubis_blend}}")

print('2/3: Loading anubis.blend...')
bpy.ops.wm.open_mainfile(filepath=anubis_blend)

print('2/3: Batch-exporting anubis animations...')
bpy.ops.export_scene.mw(
    filepath=anubis_nif,
    export_animations=True,
    extract_keyframe_data=True,
    export_all_actions=True,
)

# The batch exporter writes the static NIF as "x" + the requested filename.
check_exists(anubis_xnif)

# Keep the smoke-test artifact at the explicit name used by step 3.
if os.path.exists(anubis_nif):
    os.remove(anubis_nif)
shutil.copy2(anubis_xnif, anubis_nif)
check_exists(anubis_nif)

kf_files = [
    os.path.join(output_dir, name)
    for name in os.listdir(output_dir)
    if name.lower().endswith('.kf')
]
if not kf_files:
    raise RuntimeError('Batch animation export produced no .kf files.')

for path in sorted(kf_files):
    print(f"OK: {{path}}")


# -------------------------------------------------------------------------
# 3. Import the NIF from step 2 and re-export it.
# -------------------------------------------------------------------------
clear_scene()

print('3/3: Importing anubis.nif...')
bpy.ops.import_scene.mw(filepath=anubis_nif)

print('3/3: Re-exporting anubis_reexport.nif...')
anubis_reexport = os.path.join(output_dir, 'anubis_reexport.nif')
bpy.ops.export_scene.mw(
    filepath=anubis_reexport,
    export_animations=True,
    extract_keyframe_data=False,
)
check_exists(anubis_reexport)

print('Smoke test completed successfully.')
"""

    subprocess.run(
        [BLENDER, "-b", "--factory-startup", "--python-expr", script],
        check=True,
    )


if __name__ == "__main__":
    build_zip()
    install_and_enable()
