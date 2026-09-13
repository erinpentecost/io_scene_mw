import bpy


class ActionProperties(bpy.types.PropertyGroup):
    batch_export: bpy.props.BoolProperty(
        name="Export",
        default=True,
        description="Include this action when batch-exporting keyframes",
    )

    @staticmethod
    def register() -> None:
        bpy.types.Action.mw = bpy.props.PointerProperty(type=ActionProperties)

    @staticmethod
    def unregister() -> None:
        del bpy.types.Action.mw
