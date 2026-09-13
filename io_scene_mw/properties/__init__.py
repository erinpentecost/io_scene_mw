import bpy

from .material_properties import MaterialProperties, TextureProperties
from .object_properties import ObjectProperties
from .action_properties import ActionProperties

classes = (
    TextureProperties,
    MaterialProperties,
    ObjectProperties,
    ActionProperties,
)

register, unregister = bpy.utils.register_classes_factory(classes)
