import bpy
import sys
from pathlib import Path
from mathutils import Matrix


class AlreadyHasLodError(Exception):
    """Raised when the imported NIF already contains a NiLODNode."""


class TooFewVerticesError(Exception):
    """Raised when the imported NIF has too few non-collision vertices to
    be worth generating LOD levels for."""


# NIFs with fewer non-collision vertices than this are skipped entirely -
# meshes this small aren't worth the cost of generating LOD levels for.
MIN_VERTEX_COUNT = 40


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


def has_existing_lod_node():
    """Return True if the currently loaded scene already contains a NiLODNode."""
    return any(obj.mw.block_type == "NiLODNode" for obj in bpy.context.scene.objects)


def find_non_collision_roots():
    """Find all non-collision scene roots (there may be more than one)."""
    roots = [obj for obj in bpy.context.scene.objects if obj.parent is None]
    non_collision = [obj for obj in roots if obj.mw.block_type != "RootCollisionNode"]
    if not non_collision:
        details = "\n".join(f"  {obj.name}: {obj.mw.block_type}" for obj in roots)
        raise RuntimeError(f"Expected at least one non-collision root, found none.\nRoot objects:\n{details}")
    return non_collision


def extract_nested_collision(root):
    """
    Detach any RootCollisionNode descendants of `root`, however deeply
    nested, and re-home them as their own top-level objects, preserving
    their original world transform.

    This runs before `root` gets moved under the LOD hierarchy, so
    collision nested anywhere inside root's subtree - not just as a direct
    child - never gets dragged along with it: collision doesn't change
    with viewing distance, so it has no business living under a
    NiLODNode.
    """
    collision_nodes = []
    stack = list(root.children)
    while stack:
        obj = stack.pop()
        if obj.mw.block_type == "RootCollisionNode":
            collision_nodes.append(obj)
            # Don't descend into a collision node's own children - it's
            # extracted as a whole subtree, not searched further.
        else:
            stack.extend(obj.children)

    for collision in collision_nodes:
        collision_world = collision.matrix_world.copy()
        collision.parent = None
        collision.matrix_world = collision_world

    return collision_nodes


def count_mesh_vertices(roots):
    """
    Count vertices across every mesh found anywhere under `roots`, however
    deeply nested. Mesh data shared by more than one object (e.g. linked
    duplicates) is only counted once.
    """
    seen_mesh_names = set()
    total = 0

    stack = list(roots)
    while stack:
        obj = stack.pop()
        if obj.type == "MESH" and obj.data is not None and obj.data.name not in seen_mesh_names:
            seen_mesh_names.add(obj.data.name)
            total += len(obj.data.vertices)
        stack.extend(obj.children)

    return total


def create_lod_container(roots):
    """
    Convert (however many non-collision roots exist, each of which may have
    its own nested collision):
        root_A
        ├── collision_A
        └── meshpart1
        root_B
        └── meshpart2

    into:
        collision_A                (preserved, now its own top-level object)
        LODContainer (NiLODNode)   (new top-level object)
            └── LOD0
                ├── root_A         (now collision-free)
                │    └── meshpart1
                └── root_B
                     └── meshpart2

    Every non-collision root is gathered under one shared LOD0, so there is
    exactly one LODContainer / LOD0 (and, once generate_lods runs, LOD1 /
    LOD2 as its siblings) for the whole file - regardless of how many
    separate non-collision roots the source had. Any RootCollisionNode,
    whether it was already its own top-level root or nested anywhere
    beneath one of these roots (at any depth), ends up (or stays) outside
    the LOD hierarchy entirely.
    """
    reference = roots[0]

    # Strip nested collision out of each root before moving it, so it's
    # never carried into LOD0 as a side effect of moving its parent.
    for root in roots:
        extract_nested_collision(root)

    container = bpy.data.objects.new("LODContainer", None)
    for collection in reference.users_collection:
        collection.objects.link(container)
        break
    else:
        bpy.context.collection.objects.link(container)

    # The container is a brand-new top-level object with no root of its own
    # to inherit a transform from, so it gets an identity transform.
    container.matrix_world = Matrix.Identity(4)
    container.mw.block_type = "NiLODNode"
    container.mw.lod_center = (0.0, 0.0, 0.0)

    lod0 = bpy.data.objects.new("LOD0", None)
    for collection in reference.users_collection:
        collection.objects.link(lod0)
        break
    else:
        bpy.context.collection.objects.link(lod0)

    lod0.matrix_world = container.matrix_world.copy()
    lod0.mw.lod_far_extent = 1500.0
    lod0_world = lod0.matrix_world.copy()
    lod0.parent = container
    lod0.matrix_world = lod0_world

    for root in roots:
        root_world = root.matrix_world.copy()
        root.parent = lod0
        root.matrix_world = root_world

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


def remove_existing_case_insensitive(output_path):
    """
    Remove any file already in output_path's directory whose name matches
    output_path's name case-insensitively (e.g. a previous run wrote
    "Armor.NIF" and this run wants to write "armor.nif").

    On case-sensitive filesystems (Linux/macOS default), a plain
    output_path.exists() check would miss such a file, leaving a stale
    duplicate alongside the newly exported one.
    """
    target_name = output_path.name.lower()
    parent = output_path.parent

    if not parent.is_dir():
        return

    for existing in parent.iterdir():
        if existing.is_file() and existing.name.lower() == target_name:
            existing.unlink()


def _unquote_yaml_string(s):
    """Remove surrounding quotes and unescape \\ and \"."""
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s.replace('\\\\', '\\').replace('\\"', '"')


def parse_materials_yaml(yaml_path):
    """
    Parse the YAML output from list_materials.py and return a set of
    absolute mesh paths that contain at least one transparent texture.
    """
    transparent_meshes = set()
    current_mesh = None
    has_transparent = False

    for raw_line in yaml_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Mesh entry line: "path":  or  "path": []
        if line.startswith('"'):
            quoted = None
            if line.endswith(': []'):
                quoted = line[:-4]
            elif line.endswith(':'):
                quoted = line[:-1]

            if quoted is not None:
                if current_mesh is not None and has_transparent:
                    transparent_meshes.add(current_mesh)

                current_mesh = _unquote_yaml_string(quoted)
                has_transparent = False
                continue

        # Texture transparency flag inside the current mesh's list
        if line == 'transparent: true':
            has_transparent = True

    # Don't forget the last mesh in the file
    if current_mesh is not None and has_transparent:
        transparent_meshes.add(current_mesh)

    return transparent_meshes


def process_file(input_path, output_path, label=None):
    print()
    print("=" * 80)
    print(f"Processing: {label if label is not None else input_path.name}")
    print("=" * 80)

    clear_scene()

    import_nif(input_path)

    if has_existing_lod_node():
        print("Skipping: input already contains a NiLODNode.")
        raise AlreadyHasLodError(f"{input_path.name} already contains a NiLODNode")

    sources = find_non_collision_roots()
    print(f"Non-collision roots ({len(sources)}):")
    for source in sources:
        print(f"  {source.name} (block type: {source.mw.block_type})")

    vertex_count = count_mesh_vertices(sources)
    print(f"Non-collision vertex count: {vertex_count}")
    if vertex_count < MIN_VERTEX_COUNT:
        print(f"Skipping: fewer than {MIN_VERTEX_COUNT} vertices.")
        raise TooFewVerticesError(
            f"{input_path.name} has only {vertex_count} vertices (< {MIN_VERTEX_COUNT})"
        )

    container = create_lod_container(sources)
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

    # If the target file already exists (e.g. from a previous run), remove it
    # first so the export below is a clean replacement. This is done
    # case-insensitively since NIF filenames may vary in case between
    # runs/sources but should still be treated as the same file. This is
    # deliberately done as late as possible (only once we know we're going to
    # write a new file) so a skipped input never touches an existing output.
    remove_existing_case_insensitive(output_path)

    export_nif(output_path)


def main():
    # Blender arguments after "--":
    #
    # blender --background --python gen_lod.py -- INPUT_DIR OUTPUT_DIR [GLOB_PATTERN] [--materials-yaml YAML_PATH]
    #
    # GLOB_PATTERN is optional and matched relative to INPUT_DIR using
    # Path.rglob(), so it's implicitly prefixed with "**/". This lets you
    # restrict processing to a subset of files, e.g.:
    #   "*.nif"        -> all .nif files anywhere under INPUT_DIR (default)
    #   "sky/*.nif"    -> only .nif files directly inside any "sky" folder
    #   "sky/**/*.nif" -> .nif files anywhere under any "sky" folder
    #
    # --materials-yaml is optional. If given, any .nif file that the YAML
    # indicates has at least one transparent texture is skipped entirely.
    args = sys.argv[sys.argv.index("--") + 1:]

    # Extract --materials-yaml flag before positional arg validation
    materials_yaml = None
    if "--materials-yaml" in args:
        idx = args.index("--materials-yaml")
        if idx + 1 >= len(args):
            raise SystemExit("--materials-yaml requires a path argument")
        materials_yaml = Path(args[idx + 1]).resolve()
        args = args[:idx] + args[idx + 2:]

    if len(args) not in (2, 3):
        raise SystemExit(
            "Usage:\n"
            "  blender --background --python gen_lod.py "
            "-- INPUT_DIR OUTPUT_DIR [GLOB_PATTERN] [--materials-yaml YAML_PATH]"
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

    # Load transparent-material blocklist if provided
    skip_meshes = set()
    if materials_yaml is not None:
        if not materials_yaml.is_file():
            raise SystemExit(f"Materials YAML not found: {materials_yaml}")
        skip_meshes = parse_materials_yaml(materials_yaml)
        print(f"Materials YAML:   {materials_yaml}")
        print(f"  {len(skip_meshes)} mesh(es) with transparent textures will be skipped")

    failures = []
    skipped = []
    skipped_transparent = []
    skipped_low_poly = []

    for input_path in nif_files:
        # Skip meshes that contain transparent materials
        if str(input_path.resolve()) in skip_meshes:
            rel = input_path.relative_to(input_dir)
            print(f"Skipping (transparent materials): {rel}")
            skipped_transparent.append(input_path)
            continue

        relative_path = input_path.relative_to(input_dir)
        output_path = output_dir / relative_path

        # Mirror the input's subdirectory structure under the output directory.
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            process_file(input_path, output_path, label=relative_path)
        except AlreadyHasLodError as exc:
            skipped.append((input_path, exc))
        except TooFewVerticesError as exc:
            skipped_low_poly.append((input_path, exc))
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
    processed = len(nif_files) - len(failures) - len(skipped) - len(skipped_transparent) - len(skipped_low_poly)
    print(f"Processed: {processed}")
    print(f"Skipped:   {len(skipped)} (already had a NiLODNode)")
    print(f"Skipped:   {len(skipped_transparent)} (transparent materials)")
    print(f"Skipped:   {len(skipped_low_poly)} (fewer than {MIN_VERTEX_COUNT} vertices)")
    print(f"Failed:    {len(failures)}")

    if skipped:
        print("\nSkipped (already had NiLODNode):")
        for path, exc in skipped:
            print(f"  {path.relative_to(input_dir)}")

    if skipped_transparent:
        print("\nSkipped (transparent materials):")
        for path in skipped_transparent:
            print(f"  {path.relative_to(input_dir)}")

    if skipped_low_poly:
        print(f"\nSkipped (fewer than {MIN_VERTEX_COUNT} vertices):")
        for path, exc in skipped_low_poly:
            print(f"  {path.relative_to(input_dir)}")

    if failures:
        print("\nFailures:")
        for path, exc in failures:
            print(f"  {path.relative_to(input_dir)}: {exc}")

        raise SystemExit(1)


if __name__ == "__main__":
    main()
