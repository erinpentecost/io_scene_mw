import bpy
import sys
from pathlib import Path


def clear_scene():
    """Remove everything from the current Blender scene."""
    bpy.ops.object.mode_set(mode="OBJECT") if bpy.context.object and bpy.context.object.mode != "OBJECT" else None

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    # Remove orphaned data created by previous imports.
    for datablocks in (
        bpy.data.meshes,
        bpy.data.curves,
        bpy.data.materials,
        bpy.data.cameras,
        bpy.data.lights,
    ):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def import_nif(filepath):
    print(f"Importing: {filepath}")

    result = bpy.ops.import_scene.mw(
        filepath=str(filepath),
        vertex_precision=0.001,
        attach_keyframe_data=False,
        discard_root_transforms=True,
        use_existing_materials=False,
    )

    if "FINISHED" not in result:
        raise RuntimeError(f"Failed to import {filepath}: {result}")


def find_non_collision_root():
    """Find the non-collision scene root."""
    roots = [obj for obj in bpy.context.scene.objects if obj.parent is None]
    non_collision = [obj for obj in roots if obj.mw.block_type != "RootCollisionNode"]
    if len(non_collision) != 1:
        details = "\n".join(f"  {obj.name}: {obj.mw.block_type}" for obj in roots)
        raise RuntimeError(f"Expected exactly one non-collision root, found {len(non_collision)}.\nRoot objects:\n{details}")
    return non_collision[0]


def get_lod_source_children(root):
    """Return direct non-collision children to put into LOD0."""
    children = [child for child in root.children if child.mw.block_type != "RootCollisionNode"]
    if not children:
        raise RuntimeError(f"No non-collision children found under {root.name}")
    return children


def create_lod_container(root):
    """
    Convert:
        root
        ├── collision
        ├── meshpart1
        └── meshpart2

    into:
        root
        ├── collision
        └── LODContainer (NiLODNode)
            └── LOD0
                ├── meshpart1
                └── meshpart2

    The original root remains the scene root so collision is outside the LOD hierarchy.
    """
    source_children = get_lod_source_children(root)

    container = bpy.data.objects.new("LODContainer", None)
    for collection in root.users_collection:
        collection.objects.link(container)
        break
    else:
        bpy.context.collection.objects.link(container)

    container.matrix_world = root.matrix_world.copy()
    container.mw.block_type = "NiLODNode"
    container.mw.lod_center = (0.0, 0.0, 0.0)

    container_world = container.matrix_world.copy()
    container.parent = root
    container.matrix_world = container_world

    lod0 = bpy.data.objects.new("LOD0", None)
    for collection in root.users_collection:
        collection.objects.link(lod0)
        break
    else:
        bpy.context.collection.objects.link(lod0)

    lod0.matrix_world = container.matrix_world.copy()
    lod0.mw.lod_far_extent = 1500.0
    lod0_world = lod0.matrix_world.copy()
    lod0.parent = container
    lod0.matrix_world = lod0_world

    for child in source_children:
        child_world = child.matrix_world.copy()
        child.parent = lod0
        child.matrix_world = child_world

    return container

def generate_lod_level(container):
    """Run the addon LOD generation operator on the container."""
    bpy.ops.object.select_all(action="DESELECT")

    container.select_set(True)
    bpy.context.view_layer.objects.active = container

    result = bpy.ops.object.mw_generate_lod_level()

    if "FINISHED" not in result:
        raise RuntimeError(
            f"LOD generation failed for {container.name}: {result}"
        )


def generate_lods(container):
    print("Generating LOD level 1...")
    generate_lod_level(container)

    print("Generating LOD level 2...")
    generate_lod_level(container)


def export_nif(filepath):
    print(f"Exporting: {filepath}")

    result = bpy.ops.export_scene.mw(
        filepath=str(filepath),
        vertex_precision=0.001,
        use_active_collection=False,
        use_selection=False,
        export_animations=True,
        randomize_animations=True,
        extract_keyframe_data=False,
        preserve_root_tranforms=False,
        preserve_material_names=True,
        strip_numeric_suffixes=False,
        create_switch_nodes=False,
    )

    if "FINISHED" not in result:
        raise RuntimeError(f"Failed to export {filepath}: {result}")


def process_file(input_path, output_path, label=None):
    print()
    print("=" * 80)
    print(f"Processing: {label if label is not None else input_path.name}")
    print("=" * 80)

    clear_scene()

    import_nif(input_path)

    source = find_non_collision_root()
    print(
        f"Non-collision root: {source.name} "
        f"(block type: {source.mw.block_type})"
    )

    container = create_lod_container(source)
    print(f"Created LOD container: {container.name}")

    generate_lods(container)

    print("LOD hierarchy:")
    for level in container.children:
        print(
            f"  {level.name} "
            f"(far extent: {level.mw.lod_far_extent})"
        )
        for child in level.children:
            print(f"    {child.name}")

    export_nif(output_path)


def main():
    # Blender arguments after "--":
    #
    # blender --background --python batch_lod.py -- INPUT_DIR OUTPUT_DIR [GLOB_PATTERN]
    #
    # GLOB_PATTERN is optional and matched relative to INPUT_DIR using
    # Path.rglob(), so it's implicitly prefixed with "**/". This lets you
    # restrict processing to a subset of files, e.g.:
    #   "*.nif"        -> all .nif files anywhere under INPUT_DIR (default)
    #   "sky/*.nif"    -> only .nif files directly inside any "sky" folder
    #   "sky/**/*.nif" -> .nif files anywhere under any "sky" folder
    args = sys.argv[sys.argv.index("--") + 1:]

    if len(args) not in (2, 3):
        raise SystemExit(
            "Usage:\n"
            "  blender --background --python batch_lod.py "
            "-- INPUT_DIR OUTPUT_DIR [GLOB_PATTERN]"
        )

    input_dir = Path(args[0]).resolve()
    output_dir = Path(args[1]).resolve()
    glob_pattern = args[2] if len(args) == 3 else "*.nif"

    if not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    nif_files = sorted(
        path for path in input_dir.rglob(glob_pattern)
        if path.is_file() and path.suffix.lower() == ".nif"
    )

    if not nif_files:
        print(f"No NIF files found in {input_dir} matching pattern {glob_pattern!r}")
        return

    print(f"Input directory:  {input_dir}")
    print(f"Output directory: {output_dir}")
    print(f"Glob pattern:     {glob_pattern}")
    print(f"Found {len(nif_files)} NIF files")

    failures = []

    for input_path in nif_files:
        relative_path = input_path.relative_to(input_dir)
        output_path = output_dir / relative_path

        # Mirror the input's subdirectory structure under the output directory.
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # If the target file already exists (e.g. from a previous run), remove
        # it first so the export below is a clean replacement.
        if output_path.exists():
            output_path.unlink()

        try:
            process_file(input_path, output_path, label=relative_path)
        except Exception as exc:
            print(
                f"\nERROR processing {input_path.name}: "
                f"{type(exc).__name__}: {exc}"
            )
            failures.append((input_path, exc))

    print()
    print("=" * 80)
    print("BATCH COMPLETE")
    print("=" * 80)
    print(f"Processed: {len(nif_files) - len(failures)}")
    print(f"Failed:    {len(failures)}")

    if failures:
        print("\nFailures:")
        for path, exc in failures:
            print(f"  {path.relative_to(input_dir)}: {exc}")

        raise SystemExit(1)


if __name__ == "__main__":
    main()
