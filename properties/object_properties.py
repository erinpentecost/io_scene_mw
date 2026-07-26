import bpy


class ObjectProperties(bpy.types.PropertyGroup):
    object_flags: bpy.props.IntProperty(name="Flags", min=0, max=65535, default=2)

    is_lod_node: bpy.props.BoolProperty(
        name="LOD Node",
        description=(
            "Export this object as a NiLODNode."
            " Children are used as LOD levels, ordered from highest to lowest detail,"
            " each visible within its own 'Far Extent' distance range"
        ),
        default=False,
    )

    lod_center: bpy.props.FloatVectorProperty(
        name="LOD Center",
        description="Center point used for LOD distance calculations, in local space",
        size=3,
        default=(0.0, 0.0, 0.0),
        subtype="XYZ",
    )

    lod_far_extent: bpy.props.FloatProperty(
        name="Far Extent",
        description=(
            "Maximum distance, in game units, at which this LOD level is visible."
            " The near distance is set automatically to the previous level's far extent (0 for the first level)"
        ),
        default=0.0,
        min=0.0,
    )

    @staticmethod
    def register() -> None:
        bpy.types.Object.mw = bpy.props.PointerProperty(type=ObjectProperties)
        bpy.types.PoseBone.mw = bpy.props.PointerProperty(type=ObjectProperties)

    @staticmethod
    def unregister() -> None:
        pass
