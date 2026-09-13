# Exporting Blender Animations to Morrowind-Compatible NIF/KF Files –  Comprehensive Guide for Plugin Developers

## Introduction

This article is designed to inform the development of a Blender Python plugin that exports skeletal animations to Morrowind-compatible file formats. It explains the binary structures, data hierarchies, and transformation mathematics that OpenMW expects when loading NIF (NetImmerse Format) skeleton files and KF (Keyframe) animation files. By understanding how OpenMW parses and interprets these formats, plugin developers can generate compliant output that integrates seamlessly with the engine.

**Target Audience**: Developers writing a Blender addon that exports characters and animations for use in OpenMW.

**Key Files Referenced**: The OpenMW source code (particularly `components/nif/`, `components/nifosg/`, and animation system code) serves as the authoritative specification.

---

## Part 1: NIF File Format Fundamentals

### 1.1 NIF File Structure Overview

A NIF file is a binary format with the following high-level structure:

```
NIF File {
  Header {
    magic_string ("Gamebryo File Format, Version ...")
    version (uint32)
    user_version (uint32)
    bethesda_version (uint32)
    endianness (uint8)
  }
  StringTable (optional, version-dependent)
  RecordTypeList (name lookup table for record type strings)
  RecordData[] (array of record instances)
  RootIndices (indices of root records in the record array)
}
```

### 1.2 NIF Version Compatibility

For Morrowind compatibility, the plugin should target:

- **NIF Version**: 10.0.1.0 (specified as four 8-bit values, typically written as `0x0A000104`)
- **User Version**: 0 (for Morrowind)
- **Bethesda Version**: 0 (not applicable for Morrowind, which predates Bethesda's custom versions)

The version format is encoded as:
```c
uint32_t version = (major << 24) | (minor << 16) | (patch << 8) | (build);
```

For Morrowind: version = 0x0A000104

### 1.3 Record Structure and Type System

Every element in a NIF file is a **Record**. Records have:
- A **record type** (string identifier, e.g., "NiNode", "NiTriShape", "NiSkinInstance")
- A **record index** (position in the record array, used for cross-record references)
- A **read() method** that parses binary data specific to that record type
- A **post() method** that performs post-processing after all records are loaded

OpenMW uses a factory pattern to instantiate records:

```c++
static std::map<std::string, CreateRecord> factories = makeFactory();
// Usage: r = factories.find("NiKeyframeController")->second();
```

### 1.4 Binary Serialization Details

All data in NIF files is serialized in **little-endian** format. The plugin must:

1. **Write primitive types** in little-endian:
   - `uint32_t`, `float`, `double`, `int16_t`, etc. are serialized as raw bytes in little-endian order
   
2. **Write vectors and lists** with a count prefix:
   ```
   List<T> {
     uint32_t count
     T[count] elements
   }
   ```

3. **Write strings** as sized strings:
   ```
   String {
     uint32_t length
     char[length] data
   }
   ```

4. **Write references** as record indices:
   ```
   Ref {
     int32_t index  // -1 means null reference
   }
   ```

---

## Part 2: Skeleton Structure – NiNode and Bone Hierarchy

### 2.1 Bone Representation: NiNode

Bones in Morrowind are represented as **NiNode** records (or NiAVObject subclasses). The hierarchical structure is defined by parent-child relationships in the `NiNode.mChildren` list.

```
NiNode {
  NiAVObject properties (base class):
    string mName                      // Bone name (e.g., "Bip01 Spine")
    uint32_t mFlags                   // Animation flags
    NiTransform mTransform            // Local transformation
    NiAVObject mParent (reference)    // Parent bone (implicit, not serialized)
  
  NiNode properties:
    Vector<NiAVObject*> mChildren     // Child bones/nodes
    Vector<NiExtraData*> mExtraData   // Optional metadata
}
```

### 2.2 NiTransform Structure

Each bone has a local `NiTransform` that encodes its position, rotation, and scale relative to its parent:

```c++
struct NiTransform {
  Matrix3 mRotation      // 3x3 rotation matrix (may include scale)
  osg::Vec3f mTranslation // 3D position vector
  float mScale            // Uniform scale factor
};

struct Matrix3 {
  float mValues[3][3]     // Column-major layout
};
```

**Critical Detail**: When writing a `Matrix3`, values are stored in **row-major order** in the binary file but interpreted as **column-major** by OpenMW:

```c
// Binary layout: mValues[0][0], mValues[0][1], mValues[0][2], mValues[1][0], ...
// Interpreted as column-major for OSG: osgMat(i, j) = mValues[j][i]
```

The `toMatrix()` method converts NiTransform to a 4x4 matrix:

```
Matrix4x4 = [
  [R00*S, R10*S, R20*S, Tx],
  [R01*S, R11*S, R21*S, Ty],
  [R02*S, R12*S, R22*S, Tz],
  [0,     0,     0,     1 ]
]
```

Where R is the rotation matrix, S is the scale, and T is the translation.

### 2.3 Bone Naming Convention

Bones must follow the Morrowind naming convention with **underscores in the file**, which OpenMW automatically converts to spaces:

```
File representation:     Runtime representation:
Bip01_Pelvis       -->  Bip01 Pelvis
Bip01_Spine        -->  Bip01 Spine
Bip01_L_UpperArm   -->  Bip01 L UpperArm
```

This conversion happens in the `ReplaceAnimationUnderscoresVisitor` class. **The plugin must use underscores in bone names in the NIF file.**

### 2.4 Root Node and the NiSkinInstance

The root bone is typically named "Bip01". The hierarchy is:

```
Bip01 (root)
├── Bip01_Pelvis
├── Bip01_Spine
│   └── Bip01_Spine1
│       └── Bip01_L_Clavicle
│           └── Bip01_L_UpperArm
│               └── Bip01_L_Forearm
│                   └── Bip01_L_Hand
└── Bip01_L_Thigh
    └── Bip01_L_Calf
        └── Bip01_L_Foot
```

A **NiSkinInstance** record binds the mesh geometry to this skeleton:

```c++
struct NiSkinInstance : public Record {
  NiSkinData* mData          // Bone weights and inverse bind matrices
  NiSkinPartition* mPartitions  // Optional geometry optimization
  NiAVObject* mRoot          // Reference to root bone (e.g., "Bip01")
  Vector<NiAVObject*> mBones // Array of all bones (in order)
};
```

The `mBones` array is critical: each bone in this array is linked by index to corresponding data in `NiSkinData.mBones`.

---

## Part 3: Mesh Skinning – NiSkinData and Vertex Weights

### 3.1 Skinning Data Structure

The **NiSkinData** record stores all per-bone weight information:

```c++
struct NiSkinData : public Record {
  NiTransform mTransform              // Overall skin transformation
  Vector<BoneInfo> mBones             // One entry per bone
  NiSkinPartition* mPartitions        // Optional
};

struct BoneInfo {
  NiTransform mTransform              // Inverse bind matrix
  BoundingSphere mBoundSphere         // For culling
  Vector<VertWeight> mWeights         // Vertex influence data
};

using VertWeight = pair<uint16_t, float>;  // (vertex_index, weight)
```

### 3.2 Inverse Bind Matrices

The **inverse bind matrix** is the most critical element for correct deformation. For each bone, the plugin must:

1. **Compute the bone's rest-pose matrix** from its transform chain:
   ```
   rest_matrix = parent_matrix * bone_local_matrix
   ```

2. **Invert this matrix**:
   ```
   inverse_bind = rest_matrix^(-1)
   ```

3. **Store it as NiTransform** by decomposing the inverse matrix back into rotation, translation, and scale.

OpenMW uses the inverse bind matrix during runtime deformation:

```
final_vertex = sum(bone_animation_matrix * inverse_bind_matrix * rest_vertex * weight)
```

If the inverse bind matrix is incorrect, deformation will be distorted or inverted.

### 3.3 Vertex Weight Storage

For each vertex influenced by a bone, the pair `(vertex_index, weight)` is added to that bone's `mWeights` list:

```
Bone 0 (Bip01_Spine) {
  mWeights = [(5, 0.8), (6, 1.0), (7, 0.5), ...]
}
Bone 1 (Bip01_Pelvis) {
  mWeights = [(0, 1.0), (1, 0.9), (7, 0.5), ...]  // Note: vertex 7 in both
}
```

**Normalization**: For a given vertex, the sum of all weights across all bones should equal 1.0:
```
sum(weight[i] for all bones influencing vertex) = 1.0
```

The plugin should normalize weights during export.

### 3.4 Writing the NiSkinData Record

Pseudocode for writing NiSkinData:

```python
def write_ni_skin_data(file, skin_data):
    # Write overall transform
    write_ni_transform(file, skin_data.transform)
    
    # Write bone count
    file.write_uint32(len(skin_data.bones))
    
    # For Morrowind (version 10.0.1.0), check version:
    if version >= 0x0A000100:
        write_bool(file, has_vertex_weights=True)
    
    # Write each bone's data
    for bone_info in skin_data.bones:
        write_ni_transform(file, bone_info.inverse_bind_matrix)
        write_bounding_sphere(file, bone_info.bounding_sphere)
        
        # Write vertex weights
        file.write_uint16(len(bone_info.weights))
        for vertex_idx, weight in bone_info.weights:
            file.write_uint16(vertex_idx)
            file.write_float(weight)
```

---

## Part 4: Animation System – KF Files and NiKeyframeData

### 4.1 KF File Overview

A KF (Keyframe) file is structurally similar to a NIF file but typically contains only animation data. The root record is typically a **NiControllerSequence** (for NIF 10.0+) or **NiSequence** (for older versions).

```
KF File {
  Header (same as NIF)
  RecordData[] (typically one NiControllerSequence root)
  RootIndices (points to animation root)
}
```

### 4.2 Animation Hierarchy: NiControllerSequence

The root record in a KF file is `NiControllerSequence`:

```c++
struct NiControllerSequence : public NiSequence {
  string mName                        // Animation name (e.g., "Walk")
  string mAccumRootName              // Root bone for accumulation
  ExtraData* mTextKeys               // Timing markers
  uint32_t mArrayGrowBy              // Reserved
  Vector<ControlledBlock> mControlledBlocks  // Per-bone animation data
  
  float mStartTime, mStopTime        // Animation time range
  // ... other properties
};
```

The `mControlledBlocks` array is crucial: each block corresponds to one animated bone and contains:

```c++
struct ControlledBlock {
  string mTargetName                 // Bone name (e.g., "Bip01 Spine")
  NiInterpolator* mInterpolator      // Animation data reference
  NiTimeController* mController      // Controller record
  // ... other fields
};
```

### 4.3 NiKeyframeData – Animation Channels

Each animated bone has an `NiKeyframeData` record containing the actual keyframe data:

```c++
struct NiKeyframeData : public Record {
  QuaternionKeyMap* mRotations       // Rotation keyframes (quaternion)
  FloatKeyMap* mXRotations           // X-axis rotation (if InterpolationType_XYZ)
  FloatKeyMap* mYRotations           // Y-axis rotation
  FloatKeyMap* mZRotations           // Z-axis rotation
  Vector3KeyMap* mTranslations       // Translation keyframes
  FloatKeyMap* mScales               // Scale keyframes
  AxisOrder mAxisOrder               // Rotation decomposition order (if XYZ mode)
};
```

### 4.4 KeyMap Structure – Keyframe Storage

Each animation channel (rotation, translation, scale) is stored as a **KeyMap**:

```c++
struct KeyMap<T> {
  uint32_t mInterpolationType        // How to interpolate (see below)
  Vector<pair<float, KeyT<T>>> mKeys // Time-keyed values
};

struct KeyT<T> {
  T mValue                           // Keyframe value
  T mInTan                           // In-tangent (for Quadratic/TCB)
  T mOutTan                          // Out-tangent (for Quadratic/TCB)
};
```

### 4.5 Interpolation Types

OpenMW supports five interpolation modes:

| Type | Value | Description | Tangent Data |
|------|-------|-------------|--------------|
| Linear | 1 | Linear interpolation | No |
| Quadratic | 2 | Bézier curve with tangents | Yes (in/out) |
| TCB | 3 | Tension-Continuity-Bias spline | Yes (computed) |
| Constant | 5 | Step function (no interpolation) | No |
| XYZ | 4 | Separate X, Y, Z float channels | No |

**Linear** is recommended for most animations and is easiest to implement.

### 4.6 Writing KeyMap Data

Pseudocode for writing a KeyMap<float> with Linear interpolation:

```python
def write_float_key_map(file, keymap):
    # Write key count
    file.write_uint32(len(keymap.keys))
    
    if len(keymap.keys) == 0:
        return
    
    # Write interpolation type
    file.write_uint32(1)  # InterpolationType_Linear
    
    # Write each keyframe
    for time, key_value in keymap.keys:
        file.write_float(time)
        file.write_float(key_value.value)
        # No tangent data for Linear interpolation
```

For rotation keyframes (quaternions):

```python
def write_quaternion_key_map(file, keymap):
    file.write_uint32(len(keymap.keys))
    
    if len(keymap.keys) == 0:
        return
    
    file.write_uint32(1)  # InterpolationType_Linear
    
    for time, key_quat in keymap.keys:
        file.write_float(time)
        # Quaternion format: (x, y, z, w)
        file.write_float(key_quat.x)
        file.write_float(key_quat.y)
        file.write_float(key_quat.z)
        file.write_float(key_quat.w)
```

### 4.7 Bone Name Matching

When a `ControlledBlock` specifies `mTargetName = "Bip01 Spine"`, OpenMW searches the skeleton for a bone with that name. **Name matching is case-sensitive and requires spaces (not underscores)** in KF files.

The plugin must:
1. Take Blender bone names (which may have any convention)
2. Map them to canonical Morrowind names with spaces
3. Write those space-separated names in the KF file

---

## Part 5: NiKeyframeController – Linking Animation to Bones

The actual animation is applied through a **NiKeyframeController** record attached to each animated bone in the .nif file:

```c++
struct NiKeyframeController : public NiSingleInterpController {
  NiKeyframeData* mData              // Points to the keyframe data
};
```

The controller is attached as an update callback to a bone node. When the animation system evaluates time `t`, it:

1. Finds the bone node in the skeleton
2. Checks if it has an `NiKeyframeController` callback
3. Queries the controller's `NiKeyframeData` for the transform at time `t`
4. Applies that transform to the bone

### 5.1 Controller Attachment in NIF Files

Controllers are attached to nodes via an extra data or callback mechanism. For older Morrowind .nif files, the controller might be stored differently, but the essential pattern is:

**Bone node → has attached controller → controller references NiKeyframeData**

### 5.2 Practical Export Strategy

For a Blender plugin exporting to Morrowind:

1. **Export to a base .nif file** with just the skeleton and mesh (no controllers)
2. **Export animations to separate .kf files** containing NiControllerSequence records
3. **OpenMW will dynamically load** the .kf files when the animation is played

This is the standard pattern and avoids complex bone-level controller attachment.

---

## Part 6: Practical Export Implementation Guide

### 6.1 Overall Export Pipeline

```
Blender Data
    ↓
1. Parse bone hierarchy → Create NiNode tree
2. Bake bone matrices (rest pose)
3. Compute inverse bind matrices
4. Weight mesh vertices to bones
5. Create NiTriShape with NiSkinInstance
    ↓
Write to .nif file
    ↓
For each animation:
1. Sample bone transforms at keyframe times
2. Convert to local transforms (relative to parent)
3. Decompose to rotation, translation, scale
4. Create NiKeyframeData for each bone
5. Create ControlledBlock for each bone
6. Create NiControllerSequence
    ↓
Write to .kf file
```

### 6.2 Key Algorithmic Steps

#### Step 1: Extract Bone Hierarchy

```python
def extract_skeleton(armature):
    bones = {}
    for bone in armature.bones:
        bones[bone.name] = {
            'name': bone.name,
            'parent': bone.parent.name if bone.parent else None,
            'local_matrix': bone.matrix_local,
            'world_matrix': armature.matrix_world @ bone.matrix_world,
        }
    return bones
```

#### Step 2: Compute Inverse Bind Matrices

```python
def compute_inverse_bind_matrices(bones, rest_pose):
    inverse_binds = {}
    for bone_name, bone_data in bones.items():
        # Bone's world-space rest transform
        rest_matrix_world = rest_pose[bone_name]['world_matrix']
        
        # Invert it
        inverse_bind = rest_matrix_world.inverted()
        
        inverse_binds[bone_name] = inverse_bind
    return inverse_binds
```

#### Step 3: Extract Vertex Weights

```python
def extract_vertex_weights(mesh, armature):
    weights_by_bone = {}
    
    for vertex in mesh.vertices:
        for group in vertex.groups:
            bone_name = armature.data.bones[group.group].name
            weight = group.weight
            
            if bone_name not in weights_by_bone:
                weights_by_bone[bone_name] = []
            
            weights_by_bone[bone_name].append((vertex.index, weight))
    
    return weights_by_bone
```

#### Step 4: Convert Bone Matrix to NiTransform

```python
def matrix_to_ni_transform(matrix):
    """Convert a 4x4 matrix to NiTransform (rotation, translation, scale)"""
    translation = matrix.translation
    
    # Extract 3x3 rotation+scale part
    rot_scale_3x3 = matrix.to_3x3()
    
    # Extract uniform scale
    scale = rot_scale_3x3[0].length  # Assumes uniform scale
    
    # Remove scale from rotation
    rotation = rot_scale_3x3.normalized()
    
    return NiTransform(rotation, translation, scale)
```

#### Step 5: Sample Animation Keyframes

```python
def sample_animation(armature, action, bone_name, frame_range, fps=30):
    """Sample a bone's animation at regular intervals"""
    keyframes_rotation = []
    keyframes_translation = []
    keyframes_scale = []
    
    for frame_index in frame_range:
        scene.frame_set(frame_index)
        bone = armature.bones[bone_name]
        
        # Get pose transform
        pose_bone = armature.pose.bones[bone_name]
        local_matrix = pose_bone.matrix_basis
        
        # Decompose
        time = frame_index / fps
        rotation = local_matrix.to_quaternion()
        translation = local_matrix.translation
        scale = local_matrix.to_scale().x  # Uniform scale
        
        keyframes_rotation.append((time, rotation))
        keyframes_translation.append((time, translation))
        keyframes_scale.append((time, scale))
    
    return keyframes_rotation, keyframes_translation, keyframes_scale
```

### 6.3 NIF File Writing Strategy

Use the NIF tools library (if available) or implement binary serialization:

```python
import struct

class NIFWriter:
    def __init__(self, filepath):
        self.file = open(filepath, 'wb')
        self.records = []
        self.record_index = {}
    
    def write_header(self, version=0x0A000104, user_version=0):
        # Write magic string
        magic = b"Gamebryo File Format, Version " + str(version).encode()
        self.file.write(struct.pack('<I', len(magic)))
        self.file.write(magic)
        
        # Write versions
        self.file.write(struct.pack('<I', version))
        self.file.write(struct.pack('<I', user_version))
        self.file.write(struct.pack('<I', 0))  # Bethesda version
        self.file.write(struct.pack('<B', 1))  # Endianness (little)
    
    def register_record(self, record):
        index = len(self.records)
        self.records.append(record)
        self.record_index[id(record)] = index
        return index
    
    def write_ni_transform(self, transform):
        # Write 3x3 rotation matrix
        for row in transform.rotation:
            for val in row:
                self.file.write(struct.pack('<f', val))
        
        # Write translation
        for val in transform.translation:
            self.file.write(struct.pack('<f', val))
        
        # Write scale
        self.file.write(struct.pack('<f', transform.scale))
    
    def finalize(self):
        # Write all records, then root indices
        # This is complex and requires multiple passes
        pass
```

### 6.4 KF File Writing Strategy

```python
class KFWriter:
    def __init__(self, filepath, animation_name="Default"):
        self.file = open(filepath, 'wb')
        self.animation_name = animation_name
        self.controlled_blocks = []
    
    def add_bone_animation(self, bone_name, keyframes_rotation, 
                          keyframes_translation, keyframes_scale):
        """Add animation data for one bone"""
        self.controlled_blocks.append({
            'bone_name': bone_name,
            'rotation': keyframes_rotation,
            'translation': keyframes_translation,
            'scale': keyframes_scale,
        })
    
    def write_keyframe_data(self, rotations, translations, scales):
        """Write NiKeyframeData record"""
        # Write rotation keyframes
        self.write_quaternion_keymap(rotations)
        
        # Write translation keyframes
        self.write_vector3_keymap(translations)
        
        # Write scale keyframes
        self.write_float_keymap(scales)
    
    def write_quaternion_keymap(self, keyframes):
        # Write count
        self.file.write(struct.pack('<I', len(keyframes)))
        
        if len(keyframes) == 0:
            return
        
        # Write interpolation type (Linear = 1)
        self.file.write(struct.pack('<I', 1))
        
        for time, quat in keyframes:
            self.file.write(struct.pack('<f', time))
            self.file.write(struct.pack('<ffff', quat.x, quat.y, quat.z, quat.w))
```

### 6.5 Handling Underscores in Bone Names

```python
def export_bone_name(name):
    """Convert Blender bone name to NIF format (underscores)"""
    return name.replace(' ', '_')

def runtime_bone_name(name):
    """Convert NIF format to runtime format (spaces)"""
    return name.replace('_', ' ')
```

---

## Part 7: Critical Implementation Details

### 7.1 Transformation Matrices: Local vs. World

**Local transform**: Relative to parent bone
**World transform**: Relative to global origin

```
world_transform = parent_world_transform * local_transform
local_transform = parent_world_transform^(-1) * world_transform
```

When exporting animations, use **local transforms** for KF files.

### 7.2 Bounding Sphere Calculation

For each bone's influence in NiSkinData:

```python
def compute_bounding_sphere(vertices_influenced, weights):
    """Compute a sphere encompassing all influenced vertices"""
    positions = [vertices_influenced[v] for v, w in weights]
    center = average(positions)
    radius = max(distance(center, pos) for pos in positions)
    return BoundingSphere(center, radius)
```

### 7.3 Quaternion Storage Order

OpenMW expects quaternions in **(x, y, z, w)** order:

```python
# Blender: Quat(w, x, y, z)
# NIF: write(x, y, z, w)
quat_blender = pose_bone.rotation_quaternion
file.write(quat_blender.x, quat_blender.y, quat_blender.z, quat_blender.w)
```

### 7.4 Handling Time and Frame Rates

Blender uses frame-based animation. When exporting:

```python
time_in_seconds = frame_number / fps
# fps is typically 24, 25, or 30
```

OpenMW evaluates animations in real time (seconds), so keyframe times must be in seconds.

### 7.5 Animation Duration

The `NiControllerSequence` doesn't explicitly store duration, but it's implicit in the keyframe range:

```python
start_time = min(t for t, _ in all_keyframes)
stop_time = max(t for t, _ in all_keyframes)
# OpenMW will loop from start_time to stop_time
```

### 7.6 Handling Missing Channels

If a bone has no rotation keyframes (e.g., only translation), the rotation KeyMap should be empty but still present:

```python
def write_keyframe_data(rotation_kf, translation_kf, scale_kf):
    # Rotation (may be empty)
    write_quaternion_keymap(rotation_kf or [])
    
    # Translation (may be empty)
    write_vector3_keymap(translation_kf or [])
    
    # Scale (may be empty)
    write_float_keymap(scale_kf or [])
```

---

## Part 8: Testing and Validation

### 8.1 Post-Export Validation

After writing a NIF file, validate:

1. **Record integrity**: All record references point to valid indices
2. **Bone hierarchy**: All bones (except root) have parents
3. **Inverse bind matrices**: Check that they're correctly inverted
4. **Vertex weights**: Verify weights sum to 1.0 per vertex
5. **Bounding spheres**: Ensure they encompass influenced vertices

### 8.2 Visual Inspection Tools

Use NifSkope to inspect exported files:
- Verify bone hierarchy structure
- Check bone transformations visually
- Inspect vertex weight painting
- Validate animation keyframes

### 8.3 Runtime Testing

Test in OpenMW:

1. **Load the .nif file**: Character should display correctly
2. **Load animations**: .kf files should play without errors
3. **Verify deformation**: Bones should deform the mesh correctly
4. **Check names**: Bone names should match Morrowind conventions

---

## Part 9: Common Pitfalls and Solutions

| Issue | Cause | Solution |
|-------|-------|----------|
| Mesh distorted or inverted | Wrong inverse bind matrix | Verify matrix inversion calculation |
| Bones don't animate | Name mismatch (spaces vs. underscores) | Use underscores in .nif, spaces in .kf |
| Animations play at wrong speed | Time not in seconds | Ensure frame-to-time conversion: `t = frame / fps` |
| Partial deformation | Vertex weights don't sum to 1.0 | Normalize weights during export |
| "Missing bone" error | Record reference points to -1 | Check all NiAVObject pointers are valid |
| Version mismatch errors | Wrong NIF version number | Target 10.0.1.0 for Morrowind (0x0A000104) |

---

## Conclusion

Exporting Blender animations to Morrowind-compatible NIF/KF files requires careful attention to:

1. **Binary format specification**: Endianness, record types, serialization order
2. **Mathematical accuracy**: Inverse bind matrices, quaternion conversion, local vs. world transforms
3. **Naming conventions**: Underscores in .nif, spaces in .kf
4. **Hierarchical structure**: Proper bone parent-child relationships
5. **Weight normalization**: Vertices weighted correctly to bones

A successful plugin will:
- Parse Blender's armature and animation data
- Compute correct inverse bind matrices
- Extract vertex weights from weight groups
- Sample animations at consistent intervals
- Serialize all data in little-endian binary format
- Generate valid OpenMW-compatible .nif and .kf files

---

## Implementation Notes for `io_scene_mw`

This section documents the conventions used by this Blender addon.  They differ from some parts of the guide above, but they are the conventions the addon actually writes and expects.

### KF Root Record

The addon writes `.kf` files with a root `NiSequenceStreamHelper`.  The helper carries the animation's `NiTextKeyExtraData`, followed by one or more `NiStringExtraData` entries and matching `NiKeyframeController` records.

For rigged meshes, the first `NiStringExtraData` is the **skeleton root name** (e.g. `Bip01`).  Subsequent entries name the controller targets.  This lets OpenMW attach the `.kf` file to the correct skeleton.

### Bone Naming

Bone names use **underscores everywhere** — in both the `.nif` skeleton and the `.kf` controller targets.  For example:

```
Blender:            Bip01 L Forearm
NIF/KF:             Bip01_L_Forearm
```

The addon converts between the two forms automatically during import and export.

### Quaternion Storage

The addon stores quaternions in `(w, x, y, z)` order, matching the `NiRotData` layout used by this version of the NIF library.

### Bone Animation Export

Bone animations are **sampled** from the evaluated pose rather than read directly from F-Curves.  At each keyframe time the addon:

1. Sets the scene frame.
2. Reads the evaluated pose bone matrix.
3. Computes the local matrix relative to the parent bone.
4. Applies the per-bone axis correction.
5. Decomposes into translation, rotation, and uniform scale.

This means constraints, IK, and drivers are respected, and hierarchical transforms are correct even when multiple bones in a chain are animated.

### Rest Pose

The exported skeleton stores the **rest pose** in each `NiNode.matrix`.  The addon does not bake the current viewport pose into the skeleton.  Animation keyframes are absolute local transforms relative to that rest pose.

### Text Keys / Animation Markers

For rigged meshes, place pose markers on the **armature action**.  The addon exports them as `NiTextKeyExtraData` attached to the skeleton root node.  Markers on individual bones are not used.

### Skinning

`NiSkinData` stores the inverse bind matrix for each bone.  The addon computes this from the axis-corrected bone rest pose, so the mesh deforms correctly in OpenMW.

Vertex weights are limited to four bones per vertex and normalized so that the weights for each vertex sum to 1.0.

### Workflow Checklist

1. Create an armature with bones named in the `Bip01_...` style for biped rigs.
2. Weight the mesh to the armature with vertex groups matching the bone names.
3. Create an action on the armature with bone keyframes.
4. Add pose markers to the armature action for animation groups.
5. Export with **Export Animations** enabled.
6. For Morrowind-style split files, enable **Extract Keyframe Data** to produce `x.nif` + `x.kf`.
