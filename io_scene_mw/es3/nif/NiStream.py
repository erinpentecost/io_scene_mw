from __future__ import annotations

from itertools import chain
from math import isclose

from es3 import nif
from .NiBinaryStream import NiBinaryStream


class NiStream:
    __slots__ = "roots",

    HEADER = b"NetImmerse File Format, Version 4.0.0.2\n"
    VERSION = 0x4000002
    TYPES = vars(nif)

    def __init__(self):
        self.roots: list[NiObject] = []

    def load(self, filepath: PathLike):
        with NiBinaryStream.reader(filepath) as stream:
            assert stream.readline() == self.HEADER
            assert stream.read_uint() == self.VERSION
            self.roots += stream.read_objects(self.TYPES)

    def save(self, filepath: PathLike):
        with NiBinaryStream.writer(filepath) as stream:
            stream.write(self.HEADER)
            stream.write_uint(self.VERSION)
            stream.write_objects(self.objects(), self.roots)

    def sort(self):
        for obj in self.objects():
            obj.sort()

    def apply_scale(self, scale: float):
        if not isclose(scale, 1.0, rel_tol=0, abs_tol=1e-6):
            for obj in self.objects():
                obj.apply_scale(scale)

    def apply_time_scale(self, scale: float):
        if not isclose(scale, 1.0, rel_tol=0, abs_tol=1e-6):
            for obj in self.objects():
                obj.apply_time_scale(scale)

    @property
    def root(self) -> NiObject | None:
        return self.roots[0] if self.roots else None

    @root.setter
    def root(self, node: NiObject | None):
        self.roots = [node]

    def roots_of_type(self, cls: type[T]) -> Iterator[T]:
        yield from (root for root in self.roots if isinstance(root, cls))

    def objects(self, iterator=chain.from_iterable) -> Iterator[NiObject]:
        yield from iterator(root._traverse({None}) for root in self.roots)

    def objects_of_type(self, cls: type[T]) -> Iterator[T]:
        return (obj for obj in self.objects() if isinstance(obj, cls))

    def find_object_by_name(self, name, object_type=None, fn=str.lower):
        name = fn(name)
        for obj in self.objects_of_type(object_type or nif.NiObjectNET):
            if fn(obj.name) == name:
                return obj

    def merge_properties(self, digits=4, ignore=(), sanitize_filenames=True):
        """..."""
        cache = {}

        def ensure_unique(obj: NiObject):
            try:
                return cache[obj]
            except KeyError:
                key = obj._astuple(digits, ignore)
                obj = cache[obj] = cache.setdefault(key, obj)
                return obj

        # flags on these properties do nothing and can interfere with the merging process
        for prop in self.objects_of_type((nif.NiMaterialProperty, nif.NiTexturingProperty)):
            prop.flags = 0

        for obj in self.objects_of_type(nif.NiAVObject):
            for i, prop in enumerate(obj.properties):
                if prop is None:
                    continue

                # We must first handle any objects referenced within the properties. For
                # now this only matters with NiTexturingProperty, which holds references
                # to multiple NiSourceTexture(s). Replace any duplicated source textures
                # with those already encountered earlier in the routine.
                if isinstance(prop, nif.NiTexturingProperty):
                    for name, slot in zip(prop.texture_keys, prop.texture_maps):
                        if not (slot and slot.source):
                            continue
                        if sanitize_filenames:
                            slot.source.sanitize_filename()
                        # merge duplicate source textures
                        slot.source = ensure_unique(slot.source)
                        # merge duplicate texturing slots
                        setattr(prop, name, ensure_unique(slot))

                # merge duplicate properties
                obj.properties[i] = ensure_unique(prop)

    def discard_text_keys(self) -> list:
        """Remove every NiTextKeyExtraData block from the stream.

        Returns the discarded blocks in traversal order.  Text keys must not
        survive in an animation-free xNIF, and stray copies are easy to create
        (e.g. one per exported action), so all of them are collected.
        """
        removed = []
        for obj in self.objects_of_type(nif.NiObjectNET):
            while True:
                text_data = obj.extra_datas.discard_type(nif.NiTextKeyExtraData)
                if text_data is None:
                    break
                removed.append(text_data)
        return removed

    def extract_keyframe_data(self, allow_empty=False) -> NiStream:
        """Extract animation data. Useful for generating 'x.nif' and 'x.kf' files.

        The KF uses the vanilla Morrowind/OpenMW ``NiSequenceStreamHelper``
        layout: the first extra-data record is the ``NiTextKeyExtraData`` and
        is not paired with a controller.  Every ``NiKeyframeController`` is
        paired with one ``NiStringExtraData`` whose string is the name of the
        NIF node targeted by that controller, and the string records are
        chained in the same order as the controller records.  No extra-data
        name is emitted for an armature/root object unless that node is
        itself one of the animation targets.
        """

        # extract text data; the xNIF keeps no copy
        text_data = next(iter(self.discard_text_keys()), None)

        if text_data is None:
            if not allow_empty:
                raise ValueError(
                    "extract_keyframe_data: no NiTextKeyExtraData object was found. "
                    "Add pose markers to the exported action (e.g. 'Idle: Start' and 'Idle: Stop')."
                )
            text_data = nif.NiTextKeyExtraData()

        # extract controllers
        kf_controllers = {}

        def extract_kf_controller(owner):
            if isinstance(owner, nif.NiObjectNET):
                if kf_controller := owner.controllers.discard_type(nif.NiKeyframeController):
                    kf_controller.target = None
                    kf_controllers[owner] = kf_controller

        for root in self.roots:
            extract_kf_controller(root)
            for node in root.descendants():
                extract_kf_controller(node)

        # create x.kf output
        output = nif.NiStream()

        # The text keys are the first extra-data record; the per-target
        # NiStringExtraData records follow, one per controller, in the same
        # order as the controllers they name.
        output.root = nif.NiSequenceStreamHelper(extra_data=text_data)

        for target, controller in kf_controllers.items():
            output.root.extra_datas.append(nif.NiStringExtraData(string_data=target.name))
            output.root.controllers.append(controller)

        return output

    def attach_keyframe_data(self, kf_data: NiStream):
        # TODO can a single node have multiple keyframe controllers? if so find_type() is not enough

        kf_root = kf_data.root
        if not isinstance(kf_root, nif.NiSequenceStreamHelper):
            raise ValueError("attach_keyframe_data: kf_data root must be a NiSequenceStreamHelper")

        kf_text_data, *kf_string_datas = kf_root.extra_datas
        if not kf_string_datas:
            # KF with no controller targets — nothing to attach.
            return

        # The vanilla/OpenMW layout chains one NiStringExtraData per
        # NiKeyframeController in controller order, with the NiTextKeyExtraData
        # first and unpaired, so the string records are either exactly as
        # numerous as the controllers (vanilla) or one more (older exports
        # that prepended the skeleton root).  The first string names the node
        # that receives the text keys; in the vanilla layout that is simply
        # the first controller target.
        num_controllers = len(list(kf_root.controllers))
        if len(kf_string_datas) == num_controllers:
            # Vanilla layout: the first controller target doubles as the
            # text-key owner.
            skeleton_root_name = kf_string_datas[0].string_data
            controller_strings = kf_string_datas
        else:
            # Legacy format: the first entry is the skeleton root, the rest
            # are controller targets.
            skeleton_root_name = kf_string_datas[0].string_data
            controller_strings = kf_string_datas[1:]

        # find the skeleton root node
        skeleton_root = self.find_object_by_name(skeleton_root_name)
        if skeleton_root is None:
            raise ValueError(f"attach_keyframe_data: unable to find skeleton root {skeleton_root_name}")

        # set skeleton root text data
        skeleton_root.extra_datas.appendleft(kf_text_data)

        # collect controllers/targets
        controllers_to_attach: dict[str, nif.NiKeyframeController] = {
            s.string_data: c for s, c in zip(controller_strings, kf_root.controllers)
        }

        # merge controllers into target objects of self
        for obj in self.objects_of_type(nif.NiObjectNET):
            new_controller = controllers_to_attach.get(obj.name)
            if new_controller is not None:
                obj.controllers.discard_type(nif.NiKeyframeController)
                obj.controllers.appendleft(new_controller)
                new_controller.target = obj


if __name__ == "__main__":
    from .NiObject import NiObject
    from es3.utils.typing import *
