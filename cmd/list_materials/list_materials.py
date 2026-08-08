"""
list_materials.py

Scans a directory of Morrowind .nif files and writes a YAML file mapping
each mesh's absolute path to the absolute paths of the "Base Texture"
textures used by its materials, along with:
  - a flag indicating whether each texture has any transparency
  - the texel density of each material (pixels per world unit)

This script must be run inside Blender so that mesh/UV data is available
for texel-density calculation:

    blender --background --python list_materials.py -- INPUT_DIR OUTPUT_YAML [GLOB_PATTERN]

It parses .nif files using the standalone `es3` NIF-parsing library for
reliable texture-path extraction, and uses Blender (via the
io_scene_mw addon) for texel-density calculation.

Requires numpy (a dependency of `es3`). ImageMagick (`magick`) is used
for the transparency check.

Usage:
    blender --background --python list_materials.py -- INPUT_DIR OUTPUT_YAML [GLOB_PATTERN]

INPUT_DIR
    Directory to search for .nif files. Somewhere in INPUT_DIR's own path
    - as INPUT_DIR itself, a direct child of it, or an ancestor of it -
    there must be a folder named "meshes" (case-insensitive). The parent
    of that "meshes" folder is treated as the "data" folder (it need not
    literally be named "data"), and `<data folder>/textures` (again
    case-insensitive) is used as the root that every mesh's "Base Texture"
    paths are resolved against.

OUTPUT_YAML
    Path to the YAML file to write. Created if missing, overwritten if it
    already exists.

GLOB_PATTERN
    Optional glob pattern, matched relative to INPUT_DIR via Path.rglob()
    (so it's implicitly prefixed with "**/"). Defaults to "*.nif".
"""

import subprocess
import sys
from pathlib import Path, PurePosixPath

# ---------------------------------------------------------------------------
# Blender imports
# ---------------------------------------------------------------------------
try:
    import bpy
    import bmesh
except ImportError:
    raise SystemExit(
        "Blender is required for texel-density calculation.\n"
        "Run this script as:\n"
        "  blender --background --python list_materials.py -- "
        "INPUT_DIR OUTPUT_YAML [GLOB_PATTERN]"
    )

# ---------------------------------------------------------------------------
# es3 setup (same as before)
# ---------------------------------------------------------------------------


class DataFolderNotFoundError(Exception):
    """Raised when no folder named "meshes" can be found in/around INPUT_DIR."""


class TexturesFolderNotFoundError(Exception):
    """Raised when the data folder has no "textures" subfolder."""


class AddonLibNotFoundError(Exception):
    """Raised when the io_scene_mw addon's "lib" directory can't be located."""


def find_addon_lib_dir(start):
    """
    Walk upward from `start` looking for a "lib" directory that contains
    the "es3" package. Works regardless of exactly where this script lives
    inside (or alongside) the io_scene_mw addon checkout.
    """
    for ancestor in (start, *start.parents):
        candidate = ancestor / "lib"
        if (candidate / "es3").is_dir():
            return candidate
    raise AddonLibNotFoundError(
        f"Could not locate the io_scene_mw addon's 'lib' directory (containing "
        f"'es3') starting from {start} and searching upward. If this script has "
        f"been moved outside the addon's repository, edit ADDON_LIB_DIR at the "
        f"top of this file to point at that 'lib' directory directly."
    )


ADDON_LIB_DIR = find_addon_lib_dir(Path(__file__).resolve().parent)
sys.path.insert(0, str(ADDON_LIB_DIR))

import es3.nif as nif  # noqa: E402  (import after sys.path setup, deliberately)

# Cache transparency checks so duplicate textures across meshes are only
# inspected once.
_TRANSPARENCY_CACHE = {}

# Cache image dimensions (width, height) read from disk via ImageMagick,
# populated together with _TRANSPARENCY_CACHE by _probe_texture().
_DIMENSIONS_CACHE = {}

# Cache texture path lookups.
_RESOLVED_PATH_CACHE = {}

# Limits how many "no match found" diagnostics calculate_texel_density_for_texture()
# will print across the whole run, so a systematic failure is visible without
# flooding the log for all 1000+ meshes.
_TEXEL_DEBUG_LIMIT = 5
_texel_debug_count = 0


# ---------------------------------------------------------------------------
# Existing helpers (unchanged logic)
# ---------------------------------------------------------------------------

def resolve_texture_path(textures_folder, rel_path):
    """
    Resolve a texture path from a NIF against the actual filesystem.

    Directory components are matched case-insensitively. The final filename
    is matched by its extensionless basename (case-insensitive), ignoring
    whatever extension the NIF claims. Returns the resolved Path with real
    on-disk casing, or None if no match is found.
    """
    cache_key = (str(textures_folder), str(rel_path))
    if cache_key in _RESOLVED_PATH_CACHE:
        return _RESOLVED_PATH_CACHE[cache_key]

    current = Path(textures_folder)
    parts = list(PurePosixPath(rel_path).parts)

    if not parts:
        _RESOLVED_PATH_CACHE[cache_key] = None
        return None

    # All parts except the last are directories.
    dir_parts = parts[:-1]
    file_part = parts[-1]
    target_stem = PurePosixPath(file_part).stem.lower()

    # Walk directory parts case-insensitively.
    for part in dir_parts:
        if not current.is_dir():
            _RESOLVED_PATH_CACHE[cache_key] = None
            return None

        try:
            entries = {
                e.name.lower(): e.name
                for e in current.iterdir()
                if e.is_dir()
            }
        except OSError:
            _RESOLVED_PATH_CACHE[cache_key] = None
            return None

        real_name = entries.get(part.lower())
        if real_name is None:
            _RESOLVED_PATH_CACHE[cache_key] = None
            return None

        current = current / real_name

    # In the final directory, find the first file whose stem matches.
    if not current.is_dir():
        _RESOLVED_PATH_CACHE[cache_key] = None
        return None

    try:
        for entry in current.iterdir():
            if entry.is_file() and entry.stem.lower() == target_stem:
                _RESOLVED_PATH_CACHE[cache_key] = entry
                return entry
    except OSError:
        pass

    _RESOLVED_PATH_CACHE[cache_key] = None
    return None


def _probe_texture(texture_path):
    """
    Run ImageMagick's `identify` once for `texture_path`, populating both
    _TRANSPARENCY_CACHE and _DIMENSIONS_CACHE from the same subprocess call.
    """
    path = Path(texture_path)
    if not path.exists():
        _TRANSPARENCY_CACHE[texture_path] = None
        _DIMENSIONS_CACHE[texture_path] = None
        return

    try:
        result = subprocess.run(
            ["magick", "identify", "-format", "%[opaque] %w %h\n", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            lines = [ln.strip() for ln in result.stdout.strip().splitlines() if ln.strip()]
            if lines:
                # Opacity: multi-page/layer images may emit one line per
                # page; if ANY page reports False we treat the whole
                # texture as having transparency.
                is_transparent = any(ln.split()[0] == "False" for ln in lines)
                _TRANSPARENCY_CACHE[texture_path] = is_transparent
                # Dimensions: use the first page/frame.
                _, w, h = lines[0].split()
                _DIMENSIONS_CACHE[texture_path] = (int(w), int(h))
                return
    except Exception:
        pass

    _TRANSPARENCY_CACHE[texture_path] = None
    _DIMENSIONS_CACHE[texture_path] = None


def check_texture_transparency(texture_path):
    """
    Return True if the image has any transparent/semi-transparent pixels,
    False if it is fully opaque, or None if the file is missing or
    ImageMagick could not determine opacity.
    """
    if texture_path not in _TRANSPARENCY_CACHE:
        _probe_texture(texture_path)
    return _TRANSPARENCY_CACHE[texture_path]


def get_texture_dimensions(texture_path):
    """
    Return (width, height) for `texture_path` as read directly from the
    file on disk via ImageMagick, or None if the file is missing or
    ImageMagick could not determine its size.

    Deliberately independent of Blender/the io_scene_mw addon's own image
    loading: if the addon's texture-path preference doesn't happen to
    point at the same data folder this script resolved textures against,
    it silently falls back to a 1x1 placeholder image rather than
    failing, which would otherwise make texel density silently wrong
    instead of cleanly absent.
    """
    if texture_path not in _DIMENSIONS_CACHE:
        _probe_texture(texture_path)
    return _DIMENSIONS_CACHE[texture_path]


def find_data_folder(input_dir):
    """
    Locate the "data" folder - the parent of a folder literally named
    "meshes" (case-insensitive) - relative to `input_dir`. Handles three
    layouts:
      1. input_dir IS the meshes folder.
      2. input_dir directly CONTAINS a meshes folder.
      3. input_dir is NESTED somewhere inside a meshes folder.
    """
    if input_dir.name.lower() == "meshes":
        return input_dir.parent

    for child in input_dir.iterdir():
        if child.is_dir() and child.name.lower() == "meshes":
            return input_dir

    for ancestor in input_dir.parents:
        if ancestor.name.lower() == "meshes":
            return ancestor.parent

    raise DataFolderNotFoundError(
        f"Could not find a 'meshes' folder as, in, or above {input_dir}"
    )


def find_textures_folder(data_folder):
    """Find the case-insensitive "textures" subfolder of `data_folder`."""
    for child in data_folder.iterdir():
        if child.is_dir() and child.name.lower() == "textures":
            return child
    raise TexturesFolderNotFoundError(f"No 'textures' folder found under {data_folder}")


def base_texture_relative_path(raw_filename):
    """
    Convert a NIF-internal "Base Texture" filename into a path relative to
    the Textures folder.

    Handles filenames that:
      - are already relative to Textures ("mytexture.bmp", "sub/tex.bmp")
      - include a leading "Data Files" component
      - include a "textures" component anywhere in the path (keeping only
        what follows its last occurrence), mirroring the convention used by
        the addon's own NiSourceTexture.sanitize_filename()
    """
    cleaned = raw_filename.strip().replace("\\", "/")
    parts = PurePosixPath(cleaned).parts

    if parts and parts[0].lower() == "data files":
        parts = parts[1:]

    lowered = [p.lower() for p in parts]
    if "textures" in lowered:
        idx = len(lowered) - 1 - lowered[::-1].index("textures")
        parts = parts[idx + 1:]

    return PurePosixPath(*parts)


# ---------------------------------------------------------------------------
# Blender helpers (borrowed / adapted from gen_lod.py)
# ---------------------------------------------------------------------------

def clear_scene():
    """Remove everything from the current Blender scene."""
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass

    try:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
    except RuntimeError:
        pass

    # Remove orphaned data created by previous imports.
    for datablocks in (
        bpy.data.meshes,
        bpy.data.curves,
        bpy.data.materials,
        bpy.data.cameras,
        bpy.data.lights,
        bpy.data.images,
    ):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def import_nif_to_blender(filepath):
    """Import a NIF file into the current Blender scene via io_scene_mw."""
    print(f"  [Blender] Importing: {filepath}")
    result = bpy.ops.import_scene.mw(
        filepath=str(filepath),
        vertex_precision=0.001,
        attach_keyframe_data=False,
        discard_root_transforms=True,
        use_existing_materials=False,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"Failed to import {filepath}: {result}")


def _normalize_path_for_compare(p):
    """Return a lower-case, resolved string for case-insensitive path comparison."""
    try:
        # Blender stores Image.filepath using its own "//"-relative-path
        # convention (relative to the .blend file). Path.resolve() doesn't
        # understand "//" and will silently resolve it against the wrong
        # base, so every comparison fails without ever raising. Expand it
        # via bpy.path.abspath() first; this is a no-op for paths that are
        # already absolute.
        p = bpy.path.abspath(p)
    except Exception:
        pass
    try:
        return str(Path(p).resolve()).lower()
    except Exception:
        return str(p).lower().replace("\\", "/")


def _texture_stem(p):
    """
    Return the lower-case filename with no directory or extension, e.g.
    'Textures/tx_de_banner.tga' -> 'tx_de_banner'.

    Used as a fallback match between our resolved absolute texture path
    and the (possibly unresolved/placeholder) path Blender's Image
    datablock reports: both ultimately derive from the same NIF-embedded
    texture filename, even when the addon couldn't locate the real file
    on disk and/or the on-disk file has a different extension than the
    NIF originally requested (e.g. a .dds replacer for an original .tga).
    """
    name = str(p).replace("\\", "/").rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name.lower()


def _iter_tex_image_nodes(node_tree, _visited=None):
    """
    Yield every TEX_IMAGE node with an assigned image inside `node_tree`,
    recursing into node groups. Some import addons nest the actual Image
    Texture node inside a reusable node group rather than placing it
    directly in the material's top-level node tree, in which case a
    non-recursive scan of `node_tree.nodes` would never find it.
    """
    if _visited is None:
        _visited = set()
    if node_tree is None or id(node_tree) in _visited:
        return
    _visited.add(id(node_tree))

    for node in node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image:
            yield node
        elif node.type == "GROUP" and node.node_tree is not None:
            yield from _iter_tex_image_nodes(node.node_tree, _visited)


def calculate_texel_density_for_texture(target_texture_path):
    """
    Calculate the average texel density (texels per world unit) for all
    mesh faces in the current scene that use *target_texture_path* as
    their base texture.

    Image dimensions come from get_texture_dimensions() - i.e. read
    directly off the resolved file on disk via ImageMagick - rather than
    from Blender's Image datablock. This matters because the io_scene_mw
    addon does its own, separate texture-path resolution when building
    materials; if that resolution doesn't find the real file (e.g. its
    texture-path preference isn't configured for this data folder, or the
    NIF requests a .tga but only a .dds replacer exists on disk), it
    silently substitutes a 1x1 placeholder image rather than failing, so
    node.image.size can't be trusted even once a matching material is
    found.

    Matching a material to `target_texture_path` is therefore done in two
    passes: first by comparing fully-resolved absolute paths (in case the
    addon *did* resolve the same file we did), then, if that finds
    nothing, by comparing bare filename stems only (ignoring directory
    and extension) - which still holds even when the addon fell back to
    a placeholder, since the placeholder's filepath preserves the
    originally-requested filename.

    Returns a float, or None if the texture is not used by any mesh with
    valid UVs, or its dimensions can't be read from disk.
    """
    global _texel_debug_count

    dims = get_texture_dimensions(target_texture_path)
    if dims is None:
        return None
    img_w, img_h = dims

    target_norm = _normalize_path_for_compare(target_texture_path)
    target_stem = _texture_stem(target_texture_path)
    densities = []
    seen_image_paths = set()

    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue

        mesh = obj.data
        if not obj.material_slots:
            continue

        # Which material slot indices reference this texture.
        #
        # NOTE: this addon assigns materials with slot.link == 'OBJECT'
        # (so that objects sharing one mesh data-block can each carry a
        # different material). That means the real material lives on
        # obj.material_slots[i].material, NOT on mesh.materials[i] -
        # mesh.materials[i] stays None for every OBJECT-linked slot, even
        # though the material_index on each face still refers to the same
        # slot index either way. MaterialSlot.material already resolves
        # correctly regardless of link mode, so read through it instead.
        matching_slot_indices = []
        for idx, slot in enumerate(obj.material_slots):
            mat = slot.material
            if not mat or not mat.node_tree:
                continue
            for node in _iter_tex_image_nodes(mat.node_tree):
                img_path = node.image.filepath
                seen_image_paths.add(img_path)
                if not img_path:
                    continue
                if (
                    _normalize_path_for_compare(img_path) == target_norm
                    or _texture_stem(img_path) == target_stem
                ):
                    matching_slot_indices.append(idx)
                    break

        if not matching_slot_indices:
            continue

        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()

        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            bm.free()
            continue

        for mat_idx in matching_slot_indices:
            total_texel_area = 0.0
            total_3d_area = 0.0

            for face in bm.faces:
                if face.material_index != mat_idx:
                    continue

                area_3d = face.calc_area()
                if area_3d < 1e-12:
                    continue

                # UV polygon area via shoelace formula.
                uv_area = 0.0
                loops = face.loops
                n = len(loops)
                for i in range(n):
                    u1, v1 = loops[i][uv_layer].uv
                    u2, v2 = loops[(i + 1) % n][uv_layer].uv
                    uv_area += u1 * v2 - u2 * v1
                uv_area = abs(uv_area) * 0.5

                total_texel_area += uv_area * img_w * img_h
                total_3d_area += area_3d

            if total_3d_area > 0:
                density = (total_texel_area / total_3d_area) ** 0.5
                densities.append(density)

        bm.free()

    if not densities:
        if _texel_debug_count < _TEXEL_DEBUG_LIMIT:
            if _texel_debug_count == 0:
                print("  [texel-density DEBUG] ---- full scene dump (first failure only) ----")
                for obj in bpy.context.scene.objects:
                    print(f"    object: {obj.name!r} type={obj.type}")
                    if obj.type != "MESH":
                        continue
                    print(f"      material slots on object: {len(obj.material_slots)}")
                    for idx, slot in enumerate(obj.material_slots):
                        mat = slot.material
                        if mat is None:
                            print(f"        [{idx}] <empty material slot>")
                            continue
                        print(
                            f"        [{idx}] link={slot.link} "
                            f"name={mat.name!r} "
                            f"node_tree={'set' if mat.node_tree else 'None'}"
                        )
                        if mat.node_tree:
                            for node in mat.node_tree.nodes:
                                extra = ""
                                if node.type == "TEX_IMAGE":
                                    extra = f" image={node.image!r} filepath={node.image.filepath!r}"
                                elif node.type == "GROUP":
                                    extra = f" node_tree={node.node_tree!r}"
                                print(f"            node: type={node.type} name={node.name!r}{extra}")
                print("  [texel-density DEBUG] ---- end scene dump ----")

            _texel_debug_count += 1
            print(f"  [texel-density DEBUG] no match for: {target_texture_path}")
            print(f"      normalized target: {target_norm}  (stem: {target_stem!r})")
            if seen_image_paths:
                print(f"      raw image filepaths seen in scene:")
                for p in sorted(seen_image_paths):
                    print(f"        {p!r} -> {_normalize_path_for_compare(p)!r}  (stem: {_texture_stem(p)!r})")
            else:
                print(f"      (no TEX_IMAGE nodes with an assigned image found in the scene at all)")
        return None
    return sum(densities) / len(densities)


# ---------------------------------------------------------------------------
# Core gathering logic
# ---------------------------------------------------------------------------

def gather_base_textures(nif_path, textures_folder):
    """
    Load `nif_path` via es3 and return a sorted list of
    (absolute_path, transparent) tuples for the "Base Texture" images
    referenced by any material in the file.
    """
    stream = nif.NiStream()
    stream.load(nif_path)

    texture_infos = set()
    for prop in stream.objects_of_type(nif.NiTexturingProperty):
        base = prop.base_texture
        if base is None or base.source is None:
            continue

        filename = base.source.filename
        if not filename:
            continue

        rel_path = base_texture_relative_path(filename)
        resolved = resolve_texture_path(textures_folder, rel_path)

        if resolved is None:
            # File not found on disk; emit the unresolved path so the user
            # knows it was referenced, but mark transparency as unknown.
            unresolved = str(textures_folder / rel_path)
            texture_infos.add((unresolved, None))
        else:
            is_transparent = check_texture_transparency(str(resolved))
            texture_infos.add((str(resolved), is_transparent))

    return sorted(texture_infos)


def gather_texel_densities(nif_path, texture_paths):
    """
    Import `nif_path` into Blender and compute a texel-density float for
    every texture in `texture_paths`.

    Returns a dict: {absolute_texture_path: float | None}
    """
    clear_scene()
    import_nif_to_blender(nif_path)

    result = {}
    for tp in texture_paths:
        result[tp] = calculate_texel_density_for_texture(tp)
    return result


def find_nif_files(input_dir, glob_pattern):
    return sorted(
        path for path in input_dir.rglob(glob_pattern)
        if path.is_file() and path.suffix.lower() == ".nif"
    )


def yaml_quote(value):
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_yaml(entries, output_path):
    """
    Hand-rolled YAML writer for a simple block-mapping-of-lists, e.g.:

        "path/to/mesh.nif":
        - path: "path/to/texture_a.dds"
          transparent: true
          texel_density: 512.345678
        - path: "path/to/texture_b.dds"
          transparent: false
          texel_density: null
        "path/to/other_mesh.nif": []

    Avoids a PyYAML dependency, since this script may run in an environment
    (e.g. Blender's bundled Python) where it isn't installed.
    """
    lines = []
    for mesh_path, texture_infos in entries:
        if texture_infos:
            lines.append(f"{yaml_quote(mesh_path)}:")
            for tex_path, transparent, texel_density in texture_infos:
                lines.append(f"- path: {yaml_quote(tex_path)}")
                if transparent is None:
                    lines.append("  transparent: null")
                else:
                    lines.append(f"  transparent: {'true' if transparent else 'false'}")
                if texel_density is None:
                    lines.append("  texel_density: null")
                else:
                    lines.append(f"  texel_density: {texel_density}")
        else:
            lines.append(f"{yaml_quote(mesh_path)}: []")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Blender arguments after "--":
    #
    # blender --background --python list_materials.py -- INPUT_DIR OUTPUT_YAML [GLOB_PATTERN]
    try:
        dash_dash = sys.argv.index("--")
        args = sys.argv[dash_dash + 1:]
    except ValueError:
        raise SystemExit(
            "Usage:\n"
            "  blender --background --python list_materials.py "
            "-- INPUT_DIR OUTPUT_YAML [GLOB_PATTERN]"
        )

    if len(args) not in (2, 3):
        raise SystemExit(
            "Usage:\n"
            "  blender --background --python list_materials.py "
            "-- INPUT_DIR OUTPUT_YAML [GLOB_PATTERN]"
        )

    input_dir = Path(args[0]).resolve()
    output_path = Path(args[1]).resolve()
    glob_pattern = args[2] if len(args) == 3 else "*.nif"

    if not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    data_folder = find_data_folder(input_dir)
    textures_folder = find_textures_folder(data_folder)

    nif_files = find_nif_files(input_dir, glob_pattern)

    print(f"Input directory:  {input_dir}")
    print(f"Data folder:      {data_folder}")
    print(f"Textures folder:  {textures_folder}")
    print(f"Output file:      {output_path}")
    print(f"Glob pattern:     {glob_pattern}")
    print(f"Found {len(nif_files)} NIF files")

    if not nif_files:
        print(f"No NIF files found in {input_dir} matching pattern {glob_pattern!r}")
        return

    entries = []
    failures = []
    texel_warnings = []

    for nif_path in nif_files:
        # 1. Fast es3 parse for texture paths & transparency
        try:
            textures = gather_base_textures(nif_path, textures_folder)
        except Exception as exc:
            print(f"ERROR processing {nif_path.name}: {type(exc).__name__}: {exc}")
            failures.append((nif_path, exc))
            continue

        # 2. Blender import for texel density
        tex_paths = [t[0] for t in textures]
        try:
            densities = gather_texel_densities(nif_path, tex_paths)
            textures = [
                (path, transparent, densities.get(path))
                for path, transparent in textures
            ]
        except Exception as exc:
            print(
                f"WARNING: Texel-density calculation failed for {nif_path.name}: "
                f"{type(exc).__name__}: {exc}"
            )
            texel_warnings.append((nif_path, exc))
            textures = [(path, transparent, None) for path, transparent in textures]

        entries.append((str(nif_path.resolve()), textures))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(entries, output_path)

    # Summarize findings
    checked = sum(1 for _, infos in entries for _, t, _ in infos if t is not None)
    transparent = sum(1 for _, infos in entries for _, t, _ in infos if t is True)
    unknown = sum(1 for _, infos in entries for _, t, _ in infos if t is None)
    density_known = sum(
        1 for _, infos in entries for _, _, d in infos if d is not None
    )

    print()
    print("=" * 80)
    print("DONE")
    print("=" * 80)
    print(f"Written: {len(entries)} mesh entries -> {output_path}")
    print(f"Failed:  {len(failures)}")
    print(f"Textures checked for transparency: {checked}")
    print(f"  - Has transparency: {transparent}")
    print(f"  - Fully opaque:     {checked - transparent}")
    print(f"  - Unknown/missing:  {unknown}")
    print(f"Texel densities calculated: {density_known}")

    if texel_warnings:
        print(f"  - Texel-density warnings: {len(texel_warnings)}")

    if failures:
        print("\nFailures:")
        for path, exc in failures:
            print(f"  {path.name}: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
