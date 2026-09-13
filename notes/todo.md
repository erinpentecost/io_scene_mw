I inspected the two `.blend` files directly and compared the Blender armature data, including the bones' armature-space head/tail positions, local head/tail positions, parent relationships, and armature object transform.

The important conclusion is: **the armature object itself is in the same place in both files. The corruption is inside the bone transforms/hierarchy, not a global armature-object translation/rotation problem.**

Here is a detailed description intended for an LLM fixing the NIF importer/exporter.

---

# Bone-position comparison: `anubis_from_ase` vs `anubis_from_nif`

## 1. Overall result

`anubis_from_ase.blend` contains **43 bones**.

`anubis_from_nif.blend` contains only **16 bones**. The surviving bones are:

```text
joint_root
joint_hip
joint_luleg
joint_llleg
joint_llleg2
joint_lanke
joint_ruleg
joint_rlleg
joint_rlleg2
joint_rankle
joint_torso
joint_luarm
joint_lgun
joint_ruarm
joint_rgun
Spot_Anubis
```

The missing bones include the hands, feet, weapon bones, effects bones, and the four `handle_*` bones. That is a separate issue from the positional corruption, but it is worth noting.

The ASE armature has a coherent skeleton:

```text
joint_root
├── joint_torso
│   ├── joint_ruarm
│   │   └── joint_rgun
│   │       └── joint_rhand
│   ├── joint_luarm
│   │   └── joint_lgun
│   │       └── joint_lhand
│   └── various attachment/effect bones
└── joint_hip
    ├── joint_luleg
    │   └── joint_llleg
    │       └── joint_llleg2
    │           └── joint_lanke
    │               └── joint_lfoot
    └── joint_ruleg
        └── joint_rlleg
            └── joint_rlleg2
                └── joint_rankle
                    └── joint_rfoot
```

The NIF version preserves the basic parent relationships of the 16 surviving bones, but **the bone transforms no longer produce a connected skeleton**.

---

# 2. The armature object itself is NOT the problem

Both files have an armature object named:

```text
OBanubis_Armature
```

Its object transform is effectively identical:

```text
location = (0, 0, 0)
rotation = (0, 0, 0)
```

Therefore, the discrepancy cannot be explained by the entire armature being moved, rotated, or scaled.

The root bone's head is also exactly the same in both:

```text
joint_root head:
    ASE = (-0.034,  2.024, 22.383)
    NIF = (-0.034,  2.024, 22.383)
```

So the importer/exporter is capable of preserving the root's position.

---

# 3. `joint_root` is already slightly wrong

ASE:

```text
head = (-0.034, 2.024, 22.383)
tail = (-0.014, 1.173, 24.544)
```

NIF:

```text
head = (-0.034, 2.024, 22.383)
tail = (-0.024, 1.599, 23.464)
```

The NIF tail is essentially the **midpoint of the original root head and tail**.

Original root length:

```text
2.32
```

NIF root length:

```text
1.16
```

So the root bone is exactly half as long.

This is a particularly useful diagnostic: the NIF roundtrip appears to have lost part of the root bone's translation/length information rather than merely applying a constant coordinate-system rotation.

---

# 4. `joint_hip`: head survives, tail does not

ASE:

```text
head = (-0.034,  2.024, 22.383)
tail = ( 2.724,  3.022, 21.181)
```

NIF:

```text
head = (-0.034,  2.024, 22.383)
tail = ( 1.370, -1.374,  0.721)
```

The hip head is correct, but its tail is dramatically wrong.

Most importantly, the ASE hip tail is exactly where both upper-leg bones begin:

```text
joint_luleg head = ( 2.724, 3.022, 21.181)
joint_ruleg head = (-2.754, 3.022, 21.181)
```

In other words, the original hip establishes the two legs correctly.

In the NIF file, the hip tail is nowhere near either leg.

---

# 5. The leg chains show cumulative positional corruption

This is the clearest evidence that the problem is in **bone-local transforms / transform composition**, rather than simply one bad global transform.

## Left leg

### `joint_luleg`

ASE:

```text
head = ( 2.724,  3.022, 21.181)
tail = ( 5.125, -0.613, 16.166)
```

NIF:

```text
head = ( 1.063,  3.933, 24.662)
tail = (-0.437, 10.366, 23.950)
```

The intended upper-left-leg bone points generally downward/right from the hip.

The NIF version points in a completely different direction.

The bone length is nevertheless almost exactly preserved:

```text
ASE = 6.64
NIF = 6.64
```

That is important.

**The exporter/importer is preserving the length of this bone but not its orientation or position.**

---

### `joint_llleg`

ASE:

```text
head = ( 5.125, -0.613, 16.166)
tail = ( 7.412,  6.114, 13.123)
```

NIF:

```text
head = (-4.200,  3.147, 28.639)
tail = (-8.135,  7.090, 23.282)
```

Again, length is preserved:

```text
ASE = 7.73
NIF = 7.73
```

But the entire bone has been moved to a completely different part of space and rotated substantially.

---

### `joint_llleg2`

ASE:

```text
head = ( 7.412,  6.114, 13.123)
tail = ( 8.815,  1.816,  3.692)
```

NIF:

```text
head = (-8.788,  6.045, 34.142)
tail = (-12.467, 13.440, 27.726)
```

Length:

```text
ASE = 10.46
NIF = 10.46
```

Again, the length survives while position/orientation is badly wrong.

The error has now become enormous because it is propagating down the hierarchy.

---

### `joint_lanke`

ASE:

```text
head = ( 8.815, 1.816, 3.692)
tail = ( 9.257,-0.074, 0.913)
```

NIF:

```text
head = (-18.091, 5.539, 38.894)
tail = (-22.308, 4.880, 41.916)
```

The NIF ankle is tens of Blender units away from its intended location.

The intended bone is only about `3.39` units long; the NIF version is about `5.23` units long, so this is also where length starts becoming corrupted.

---

# 6. Right leg has the same failure pattern

The right leg exhibits essentially the same problem.

### `joint_ruleg`

ASE:

```text
head = (-2.754, 3.022, 21.181)
tail = (-5.140,-0.613,16.166)
```

NIF:

```text
head = (-2.888, 0.740,22.611)
tail = (-7.839, 4.382,20.107)
```

The upper-leg bone is already wrong, although its position is initially much closer than the deeper bones.

### `joint_rlleg`

ASE:

```text
head = (-5.140,-0.613,16.166)
tail = (-7.427, 6.114,13.123)
```

NIF:

```text
head = (-4.167, 3.159,28.658)
tail = (-4.437,10.074,25.217)
```

Length remains almost exactly correct:

```text
7.73 → 7.73
```

### `joint_rlleg2`

ASE:

```text
head = (-7.427,6.114,13.123)
tail = (-8.830,1.816,3.692)
```

NIF:

```text
head = (-9.384,5.535,33.842)
tail = (-11.997,13.807,27.999)
```

Length again remains correct:

```text
10.46 → 10.46
```

### `joint_rankle`

ASE:

```text
head = (-8.830,1.816,3.692)
tail = (-9.272,-0.074,0.913)
```

NIF:

```text
head = (-11.140,11.107,42.516)
tail = (-12.043,13.114,47.260)
```

Again, the ankle has migrated dramatically upward and away from the body.

---

# 7. The two leg chains strongly suggest a local-vs-armature transform bug

This is probably the most important observation for fixing the plugin.

In the good ASE-derived Blender file, child bones line up naturally:

```text
joint_hip tail
    =
joint_luleg head

joint_luleg tail
    =
joint_llleg head

joint_llleg tail
    =
joint_llleg2 head

joint_llleg2 tail
    ≈
joint_lanke head
```

and similarly on the right.

In the NIF-derived Blender file this relationship is broken:

```text
joint_hip tail
    != joint_luleg head

joint_luleg tail
    != joint_llleg head

joint_llleg tail
    != joint_llleg2 head

...
```

The deeper bones progressively fly farther away.

Yet many individual bone **lengths remain exactly correct**.

That combination is a strong indication that:

* the bone's length is being recovered correctly;
* something about its rotation/orientation is being recovered incorrectly;
* the resulting local transform is then being composed with the parent's transform incorrectly;
* the error accumulates down the hierarchy.

This does **not** look like a simple uniform X/Y/Z axis swap.

---

# 8. The arms are even more obviously rotated incorrectly

## `joint_luarm`

ASE:

```text
head = ( 6.257, 3.992,30.000)
tail = (10.434,4.036,25.996)
```

NIF:

```text
head = (-2.940,-1.920,29.604)
tail = (-4.581,-7.327,28.362)
```

The intended left upper arm starts on the **positive-X side** of the torso.

The NIF version is on the **negative-X side**.

It has effectively crossed over to the other side of the character, in addition to being rotated.

Its length is preserved:

```text
5.79 → 5.79
```

## `joint_ruarm`

ASE:

```text
head = (-6.328,3.980,30.000)
tail = (-10.610,4.036,25.996)
```

NIF:

```text
head = (4.799,6.615,24.537)
tail = (8.375,6.935,19.903)
```

Again, the intended right upper arm is on negative X, while the NIF version is on positive X.

So the two upper arms are not merely slightly displaced: **their recovered orientations/locations are effectively reversed across the body.**

This is a particularly strong clue that a rotation/quaternion/matrix conversion is wrong.

---

# 9. The guns lose their intended orientation and length

## `joint_lgun`

ASE:

```text
head = (10.434,4.036,25.996)
tail = (10.434,-8.375,25.996)
length = 12.41
```

This is a long, almost perfectly vertical Y-axis bone.

NIF:

```text
head = (0.430,-1.862,24.901)
tail = (2.615,-2.910,26.480)
length = 2.89
```

The NIF bone is:

* in the wrong position;
* pointing in a completely different direction;
* only about one quarter of the original length.

## `joint_rgun`

ASE:

```text
head = (-10.610,4.036,25.996)
tail = (-10.610,-8.375,25.996)
length = 12.41
```

NIF:

```text
head = (3.254,1.181,22.970)
tail = (5.468,0.120,24.572)
length = 2.93
```

Same failure.

The arms therefore aren't just translated incorrectly: **the recovered bone rotation and/or bone matrix is fundamentally different.**

---

# 10. `joint_torso` is also badly wrong

ASE:

```text
head = (-0.014,1.173,24.544)
tail = (-6.328,3.980,30.000)
length = 8.80
```

NIF:

```text
head = (0.015,-0.137,21.533)
tail = (3.437,-1.140,18.338)
length = 4.79
```

The torso bone:

* starts in the wrong place;
* points in the wrong direction;
* is substantially shorter.

This is significant because `joint_torso` is the parent of the arms and many attachment bones. Once its transform is wrong, all of its descendants will inherit that wrong coordinate frame.

---

# 11. `Spot_Anubis` is also transformed incorrectly

ASE:

```text
head = (-0.038,-6.176,27.892)
tail = (-0.038,-6.076,27.892)
```

It is essentially a tiny marker bone.

NIF:

```text
head = (-6.273,4.686,19.974)
tail = (-4.464,3.818,21.280)
```

The NIF version has turned a tiny ~0.1-unit marker into a ~2.4-unit diagonal bone somewhere else in space.

This is another indication that the problem isn't simply "bone endpoints were translated a little."

---

# 12. Bone local coordinates also reveal the problem

The good ASE file frequently has child bones with local heads of:

```text
(0, 0, 0)
```

For example:

```text
joint_luleg
joint_llleg
joint_llleg2
joint_lanke
joint_lgun
joint_rgun
...
```

This is consistent with a normal parent-relative skeleton: the child begins at its parent's coordinate origin/end and its local transform establishes the direction and length.

In the NIF file, those same bones have large nonzero local head positions.

Examples:

```text
joint_luleg:
    NIF local head = (0, -1.563, -3.169)

joint_llleg:
    NIF local head = (0, -6.643, -6.643)

joint_llleg2:
    NIF local head = (0, -7.729, -7.729)

joint_lanke:
    NIF local head = (0,-10.459,-10.459)

joint_rlleg:
    NIF local head = (0,-6.638,-6.637)

joint_rlleg2:
    NIF local head = (0,-7.729,-7.729)

joint_rankle:
    NIF local head = (0,-10.459,-10.459)
```

This is a very strong diagnostic.

The good skeleton has child bones whose local coordinate systems are arranged so that the hierarchy connects naturally. The NIF roundtrip has introduced substantial translations into the child bones' local transforms.

Those translations then get compounded through the hierarchy, producing the progressively larger errors visible in armature-space coordinates.

---

# 13. The bone rotation matrices are radically different

For example, `joint_lgun` in ASE has an extremely simple rotation matrix:

```text
ASE:

[-1,  0,  0]
[ 0, -1,  0]
[ 0,  0,  1]
```

The NIF version has:

```text
NIF:

[-0.625, -0.650,  0.433]
[ 0.755, -0.362,  0.546]
[-0.198,  0.668,  0.717]
```

Likewise, `joint_luarm` is approximately:

```text
ASE:

[ 0.010, -1.000,  0.000]
[ 0.722,  0.007,-0.692]
[ 0.692,  0.007, 0.722]
```

but becomes:

```text
NIF:

[-0.762,  0.356,-0.542]
[-0.284,-0.935,-0.215]
[-0.582,-0.010, 0.813]
```

And `joint_torso` changes from:

```text
ASE:

[ 0.406,  0.914,  0.000]
[-0.717,  0.319,  0.620]
[ 0.566,-0.252,  0.785]
```

to:

```text
NIF:

[-0.440,  0.607,-0.662]
[ 0.715,-0.209,-0.667]
[-0.543,-0.767,-0.341]
```

So the NIF roundtrip is producing genuinely different bone orientation matrices, not merely a different representation of the same rotations.

---

# 14. Error grows with hierarchy depth

Approximate armature-space head-position error:

| Bone           | Position error |
| -------------- | -------------: |
| `joint_root`   |              0 |
| `joint_hip`    |              0 |
| `joint_ruleg`  |            2.7 |
| `joint_luleg`  |            4.0 |
| `joint_rlleg`  |           13.1 |
| `joint_llleg`  |           16.0 |
| `joint_rlleg2` |           20.8 |
| `joint_llleg2` |           26.5 |
| `joint_rankle` |           40.0 |
| `joint_lanke`  |           44.5 |

That pattern is extremely important.

The farther down the skeleton the bone is, the farther it tends to drift from its intended position.

This is exactly what one would expect from a **bad parent-relative transform being recursively composed**.

It is not what one would expect from a single fixed global transform.

---

# 15. Most likely nature of the bug

The evidence points toward the NIF roundtrip doing something like:

```text
NIF bone transform
    ↓
incorrectly interpreted as Blender local transform
    ↓
Blender parent transform applied
    ↓
incorrect child position/orientation
    ↓
next child inherits that incorrect transform
    ↓
error compounds down the chain
```

The critical distinction for the implementation is between:

1. **bone-local transform relative to its parent**, and
2. **bone transform/position in armature/object space**.

The ASE-generated Blender armature has coherent parent-relative transforms and connected armature-space endpoints.

The NIF-generated Blender armature appears to be reconstructing bone transforms in the wrong space and then allowing Blender to apply the parent transform again.

The fact that individual bone lengths often survive while positions and orientations don't is particularly suggestive of a **matrix/quaternion decomposition or local-vs-global transform error**, rather than a simple scale problem.

---

# 16. There is no single X/Y/Z conversion that fixes this

Do **not** interpret the problem as simply:

```text
x -> y
y -> z
z -> x
```

or:

```text
x -> -x
```

The root position is already correct, and the leg bones have the correct lengths while their orientations differ substantially.

Furthermore, the errors differ by bone and grow down the hierarchy.

The arms also cross from one side of the body to the other.

Therefore the fix needs to be made in the **construction of each bone's local transform and/or the conversion between NIF bone transforms and Blender bone transforms**, not by applying a global corrective transform to the finished armature.

---

# 17. Particularly useful expected positions

An implementation/test suite should be able to verify at least these armature-space positions after the NIF roundtrip.

### Root

```text
joint_root.head = (-0.034,  2.024, 22.383)
joint_root.tail = (-0.014,  1.173, 24.544)
```

### Hip

```text
joint_hip.head = (-0.034,  2.024, 22.383)
joint_hip.tail = ( 2.724,  3.022, 21.181)
```

### Left leg

```text
joint_luleg.head  = ( 2.724,  3.022, 21.181)
joint_luleg.tail  = ( 5.125, -0.613, 16.166)

joint_llleg.head  = ( 5.125, -0.613, 16.166)
joint_llleg.tail  = ( 7.412,  6.114, 13.123)

joint_llleg2.head = ( 7.412,  6.114, 13.123)
joint_llleg2.tail = ( 8.815,  1.816,  3.692)

joint_lanke.head  = ( 8.815,  1.816,  3.692)
joint_lanke.tail  = ( 9.257, -0.074,  0.913)
```

### Right leg

```text
joint_ruleg.head  = (-2.754,  3.022, 21.181)
joint_ruleg.tail  = (-5.140, -0.613, 16.166)

joint_rlleg.head  = (-5.140, -0.613, 16.166)
joint_rlleg.tail  = (-7.427,  6.114, 13.123)

joint_rlleg2.head = (-7.427,  6.114, 13.123)
joint_rlleg2.tail = (-8.830,  1.816,  3.692)

joint_rankle.head = (-8.830,  1.816,  3.692)
joint_rankle.tail = (-9.272, -0.074,  0.913)
```

### Torso/arms

```text
joint_torso.head = (-0.014, 1.173, 24.544)
joint_torso.tail = (-6.328, 3.980, 30.000)

joint_luarm.head = ( 6.257, 3.992, 30.000)
joint_luarm.tail = (10.434, 4.036, 25.996)

joint_lgun.head = (10.434, 4.036, 25.996)
joint_lgun.tail = (10.434,-8.375, 25.996)

joint_ruarm.head = (-6.328, 3.980, 30.000)
joint_ruarm.tail = (-10.610,4.036, 25.996)

joint_rgun.head = (-10.610,4.036,25.996)
joint_rgun.tail = (-10.610,-8.375,25.996)
```

The key invariant is that **child bone heads coincide with their parent's intended tail**.

---

## Bottom line for the plugin-fixing LLM

The NIF → Blender conversion is **not preserving the Blender armature's bone-local transforms**.

The armature object itself is correct and the root head is correct. The failure begins with bone tails/orientations and becomes progressively worse down the hierarchy. Many bones retain their correct lengths, but their rotation matrices and parent-relative translations are wrong. As a result, Blender recursively composes incorrect child transforms and the skeleton "unwinds" away from the original.

The strongest debugging targets are therefore:

1. **How NIF bone transforms are converted into Blender EditBone/Bone transforms.**
2. **Whether a NIF transform is being treated as armature-space when it is actually parent-relative, or vice versa.**
3. **Whether the parent transform is being applied twice.**
4. **Matrix multiplication order / transpose / inverse when converting NIF rotation + translation to Blender.**
5. **Quaternion-to-matrix conversion and coordinate-system conversion.**
6. **How bone head position is derived separately from bone orientation/length.**
7. **Whether the exporter is writing the correct parent-relative NIF transform in the first place.**

A particularly good regression test is the leg chain: if `joint_luleg`, `joint_llleg`, `joint_llleg2`, and `joint_lanke` don't form a connected chain at exactly the positions above after a NIF roundtrip, the transform conversion is still wrong.
