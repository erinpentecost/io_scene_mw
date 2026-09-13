import os
import sys

import bpy

bl_info = {
    "name": "Morrowind (.nif)",
    "author": "Greatness7",
    "version": (0, 8, 120),
    "blender": (3, 6, 0),
    "location": "File > Import/Export > Morrowind (.nif)",
    "description": "Import/Export files for Morrowind",
    "wiki_url": "https://blender-morrowind.readthedocs.io/",
    "tracker_url": "https://github.com/Greatness7/io_scene_mw/issues",
    "category": "Import-Export",
}

# Make bundled libraries importable: es3 lives next to this file, and any
# legacy helpers live in ./lib.
package_dir = os.path.dirname(__file__)
if package_dir not in sys.path:
    sys.path.append(package_dir)

lib = os.path.join(package_dir, "lib")
if lib not in sys.path:
    sys.path.append(lib)

submodules = (
    "preferences",
    "properties",
    "operators",
    "panels",
)

register, unregister = bpy.utils.register_submodule_factory(__name__, submodules)
