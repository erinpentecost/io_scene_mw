import re

import bpy


class GenerateLODLevel(bpy.types.Operator):
    bl_idname = "object.mw_generate_lod_level"
    bl_options = {"REGISTER", "UNDO"}
    bl_label = "Generate LOD Level"
    bl_description = (
        "Join, decimate, and re-split (by material) a copy of this LOD level's geometry,"
        " adding the result as a new, lower-detail sibling level. The original is left untouched"
    )

    ratio: bpy.props.FloatProperty(
        name="Ratio",
        description="Decimate ratio applied to the merged geometry",
        default=0.75,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )

    extent_increment: bpy.props.FloatProperty(
        name="Extent Increment",
        description="Far Extent for the new LOD level, added on top of the selected level's Far Extent",
        default=1500.0,
        min=0.0,
    )

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return (
            (ob is not None)
            and (ob.parent is not None)
            and (ob.parent.mw.block_type == "NiLODNode")
        )

    def execute(self, context):
        ob = context.active_object
        parent = ob.parent

        # select the whole subtree so bpy.ops.object.duplicate() keeps it intact
        # (duplicated children are automatically re-parented onto their duplicated parents)
        bpy.ops.object.select_all(action="DESELECT")
        for o in (ob, *self.iter_descendants(ob)):
            o.select_set(True)
        context.view_layer.objects.active = ob

        bpy.ops.object.duplicate(linked=False)
        duplicates = list(context.selected_objects)

        meshes = [o for o in duplicates if o.type == "MESH"]
        others = [o for o in duplicates if o.type != "MESH"]

        # any duplicated wrapper empties were only needed to keep the subtree intact
        # while duplicating; discard them now that the meshes stand on their own
        for o in others:
            bpy.data.objects.remove(o, do_unlink=True)

        if not meshes:
            self.report({"WARNING"}, "No mesh data found under the selected object")
            return {"CANCELLED"}

        # join the duplicated meshes into one
        bpy.ops.object.select_all(action="DESELECT")
        for o in meshes:
            o.select_set(True)
        context.view_layer.objects.active = meshes[0]
        bpy.ops.object.join()
        joined = context.view_layer.objects.active

        # decimate
        modifier = joined.modifiers.new(name="Decimate", type="DECIMATE")
        modifier.ratio = self.ratio
        bpy.ops.object.modifier_apply(modifier=modifier.name)

        # separate by material
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.separate(type="MATERIAL")
        bpy.ops.object.mode_set(mode="OBJECT")

        pieces = list(context.selected_objects)
        name = self.next_lod_name(parent, ob.name)
        far_extent = ob.mw.lod_far_extent + self.extent_increment

        if len(pieces) == 1:
            pieces[0].name = name
            pieces[0].mw.lod_far_extent = far_extent
            self.reparent_keep_transform(pieces[0], parent)
        else:
            container = bpy.data.objects.new(name, None)
            context.collection.objects.link(container)
            container.matrix_world = ob.matrix_world
            container.mw.lod_far_extent = far_extent
            self.reparent_keep_transform(container, parent)

            for piece in pieces:
                self.reparent_keep_transform(piece, container)

        return {"FINISHED"}

    @staticmethod
    def iter_descendants(ob):
        for child in ob.children:
            yield child
            yield from GenerateLODLevel.iter_descendants(child)

    @staticmethod
    def reparent_keep_transform(ob, new_parent):
        world = ob.matrix_world.copy()
        ob.parent = new_parent
        ob.matrix_world = world

    @staticmethod
    def next_lod_name(parent, base_name):
        match = re.match(r"^(.*?)(\d+)$", base_name)
        prefix, number = (match.group(1), int(match.group(2))) if match else (base_name, 0)

        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        used = {
            int(m.group(1))
            for child in parent.children
            if (m := pattern.match(child.name))
        }

        n = number + 1
        while n in used:
            n += 1
        return f"{prefix}{n}"
