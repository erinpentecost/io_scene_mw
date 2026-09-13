# Pose Markers for Animations: A Technical Guide for Morrowind-Compatible NIF Exporters

## Overview

Pose markers are a critical component in the OpenMW animation pipeline for Morrowind-compatible models. They serve as temporal synchronization points that map animation frames to semantic events and state transitions. When properly configured, pose markers enable smooth animation playback, sound effects, and game state updates at precise moments during an animation sequence.

## What Are Pose Markers?

Pose markers are **action-scoped timeline markers** in Blender that define named keyframes within an animation action. Unlike global timeline markers, pose markers are stored per action and represent specific moments in animation playback that require game engine responses.

### Key Characteristics

- **Scope**: Per-action (not global to the Blender file)
- **Naming Convention**: `{action_name}: {event_type}` (e.g., `attack1: start`, `walkforward: stop`)
- **Timing**: Specified in animation frames (30 fps in Blender for Morrowind)
- **Purpose**: Define animation boundaries and trigger in-game events

## The Animation Pipeline

### 1. NIF File Storage: NiTextKeyExtraData

Pose markers are ultimately stored in NIF files as `NiTextKeyExtraData` structures. Here's the C++ definition:

```cpp
// From components/nif/extra.hpp
struct NiTextKeyExtraData : public Extra
{
    struct TextKey
    {
        float mTime;      // Time in seconds
        std::string mText; // Event string (e.g., "attack1: hit")

        void read(NIFStream* nif);
    };
    std::vector<TextKey> mList;

    void read(NIFStream* nif) override;
};
```

When reading from a NIF file:

```cpp
// From components/nif/extra.cpp
void NiTextKeyExtraData::TextKey::read(NIFStream* nif)
{
    nif->read(mTime);  // Float value representing time in seconds
    nif->read(mText);  // String ID reference to event name
}

void NiTextKeyExtraData::read(NIFStream* nif)
{
    Extra::read(nif);
    nif->readVectorOfRecords<uint32_t>(mList);  // Read array of TextKey records
}
```

### 2. Runtime Storage: TextKeyMap

At runtime, OpenMW uses a `TextKeyMap` structure to efficiently query and iterate over text keys:

```cpp
// From components/sceneutil/textkeymap.hpp
class TextKeyMap
{
public:
    using ConstIterator = std::multimap<float, std::string>::const_iterator;

    auto begin() const noexcept { return mTextKeyByTime.begin(); }
    auto end() const noexcept { return mTextKeyByTime.end(); }
    
    auto lowerBound(float time) const { return mTextKeyByTime.lower_bound(time); }
    auto upperBound(float time) const { return mTextKeyByTime.upper_bound(time); }

    void emplace(float time, std::string&& textKey)
    {
        // Extract group name (e.g., "attack1" from "attack1: hit")
        const auto separator = textKey.find(": ");
        if (separator != std::string::npos)
            mGroups.emplace(textKey.substr(0, separator));

        mTextKeyByTime.emplace(time, std::move(textKey));
    }

    bool hasGroupStart(std::string_view groupName) const 
    { 
        return mGroups.count(groupName) > 0; 
    }

private:
    std::set<std::string, std::less<>> mGroups;
    std::multimap<float, std::string> mTextKeyByTime;
};
```

**Key insight**: Text keys are stored in a multimap indexed by time, allowing O(log n) lookup for time-based queries.

## Animation Playback and Text Key Handling

### Animation State Management

When an animation plays, OpenMW maintains the following state:

```cpp
// From apps/openmw/mwrender/animation.hpp
struct AnimState
{
    std::shared_ptr<AnimSource> mSource;
    float mStartTime = 0;      // Animation start in seconds
    float mLoopStartTime = 0;  // Loop restart point
    float mLoopStopTime = 0;   // Loop end point
    float mStopTime = 0;       // Animation end in seconds
    
    std::shared_ptr<float> mTime = std::make_shared<float>(0.0f);
    float mSpeedMult = 1;
    
    bool mPlaying = false;
    bool mLoopingEnabled = true;
    uint32_t mLoopCount = 0;
};
```

### Text Key Event Dispatch

Text keys are processed during animation playback through a listener pattern:

```cpp
// From apps/openmw/mwrender/animation.hpp
class TextKeyListener
{
public:
    virtual void handleTextKey(
        std::string_view groupname, 
        SceneUtil::TextKeyMap::ConstIterator key, 
        const SceneUtil::TextKeyMap& map) = 0;
    
    virtual ~TextKeyListener() = default;
};
```

When an animation plays, the engine iterates through all text keys in the animation's time range:

```cpp
// From apps/openmw/mwrender/animation.cpp
void Animation::handleTextKey(AnimState& state, std::string_view groupname,
    SceneUtil::TextKeyMap::ConstIterator key, const SceneUtil::TextKeyMap& map)
{
    std::string_view evt = key->second;

    // Parse loop control events
    if (evt.starts_with(groupname) && evt.substr(groupname.size()).starts_with(": "))
    {
        size_t off = groupname.size() + 2;
        if (evt.substr(off) == "loop start")
            state.mLoopStartTime = key->first;
        else if (evt.substr(off) == "loop stop")
            state.mLoopStopTime = key->first;
    }

    // Dispatch to registered listener
    try
    {
        if (mTextKeyListener != nullptr)
            mTextKeyListener->handleTextKey(groupname, key, map);
    }
    catch (std::exception& e)
    {
        Log(Debug::Error) << "Error handling text key " << evt << ": " << e.what();
    }
}
```

### Character Controller Processing

The character controller handles various text key events:

```cpp
// From apps/openmw/mwmechanics/character.cpp
void CharacterController::handleTextKey(
    std::string_view groupname, 
    SceneUtil::TextKeyMap::ConstIterator key, 
    const SceneUtil::TextKeyMap& map)
{
    std::string_view evt = key->second;

    // Sound playback
    if (evt.substr(0, 7) == "sound: ")
    {
        std::string soundId = evt.substr(7);
        mSoundManager->playSound3D(mPtr, ESM::RefId::stringRefId(soundId), 1.0f, 1.0f);
        return;
    }

    // Sound generation (footsteps, etc.)
    if (evt.substr(0, 10) == "soundgen: ")
    {
        std::string_view soundgen = evt.substr(10);
        // Parse optional volume and pitch modifiers
        // e.g., "soundgen: footstep 1.0 1.0"
        // ...
    }

    // Equipment state changes
    if (evt.substr(0, groupname.size()) != groupname || 
        evt.substr(groupname.size(), 2) != ": ")
        return;

    std::string_view action = evt.substr(groupname.size() + 2);
    if (action == "equip attach")
    {
        // Show carried items
        if (groupname == "shield")
            mAnimation->showCarriedLeft(true);
    }
    else if (action == "unequip detach")
    {
        // Hide carried items
        if (groupname == "shield")
            mAnimation->showCarriedLeft(false);
    }
    else if (action == "chop hit" || action == "slash hit" || 
             action == "thrust hit" || action == "hit")
    {
        // Handle weapon impact
        // ...
    }
}
```

## Standard Text Key Events

Based on OpenMW's implementation, here are the standard text key events:

| Event Type | Format | Example | Purpose |
|-----------|--------|---------|---------|
| **Animation Boundary** | `{group}: start` / `{group}: stop` | `attack1: start`, `attack1: stop` | Define animation time range |
| **Loop Control** | `{group}: loop start` / `{group}: loop stop` | `idle: loop start`, `idle: loop stop` | Define loopable section |
| **Sound Effect** | `sound: {sound_id}` | `sound: magic_cast` | Trigger 3D sound effect |
| **Sound Generation** | `soundgen: {type} [volume] [pitch]` | `soundgen: left 1.0 1.0`, `soundgen: footstep` | Trigger procedural sounds |
| **Equipment Visibility** | `{group}: equip attach` / `{group}: unequip detach` | `shield: equip attach` | Show/hide carried items |
| **Hit Events** | `{group}: chop hit` / `{group}: hit` | `attack1: hit`, `attack1: slash hit` | Handle weapon impacts |

## Export Format Considerations

When exporting from Blender to NIF, the COLLADA exporter converts pose markers to text keys:

```
Pose Marker:           Text Key Output:
Frame: 1               Time: 0.033333 (1 frame @ 30fps = 1/30 sec)
Frame: 30              Time: 1.000000 (30 frames @ 30fps = 30/30 sec)
```

### Strip Transformations

If animations are placed in NLA Editor strips with frame offsets or scaling, the exporter applies these transformations:

```
Frame offset: 10 frames
Strip scale: 1.5x
Pose marker frame: 5
→ Effective frame: (5 × 1.5) + 10 = 17.5 frames
→ Time value: 17.5 / 30 = 0.583333 seconds
```

## Implementation Guide for Blender Plugin

### Reading Text Keys from NIF Files

```python
def read_text_keys_from_nif(nif_file):
    """
    Extract NiTextKeyExtraData from a NIF file.
    Returns dict mapping animation group names to list of (time, event) tuples.
    """
    text_keys = {}
    
    # Parse NIF file and find NiTextKeyExtraData blocks
    for extra_data in nif_file.get_extra_data():
        if extra_data.type == "NiTextKeyExtraData":
            for text_key in extra_data.keys:
                time = text_key.time  # float in seconds
                text = text_key.text   # string like "attack1: start"
                
                # Extract group name
                if ": " in text:
                    group, event = text.split(": ", 1)
                    if group not in text_keys:
                        text_keys[group] = []
                    text_keys[group].append((time, event))
    
    return text_keys
```

### Creating Pose Markers in Blender

```python
def create_pose_markers_from_text_keys(action, text_keys_dict, frame_rate=30):
    """
    Create pose markers in a Blender action from text key data.
    """
    action_name = action.name
    
    if action_name not in text_keys_dict:
        return
    
    for time, event in text_keys_dict[action_name]:
        # Convert time (seconds) to frames
        frame = time * frame_rate
        
        # Create pose marker
        marker = action.pose_markers.new(f"{action_name}: {event}")
        marker.frame = int(round(frame))
```

### Exporting Pose Markers to Text Key Format

```python
def export_pose_markers_to_text_keys(action, strip=None, frame_rate=30):
    """
    Extract pose markers from action and convert to OpenMW text key format.
    Accounts for NLA strip frame offset and scaling.
    
    Args:
        action: Blender Action object
        strip: Optional NLA strip (for frame offset/scale)
        frame_rate: Animation frame rate (default 30fps)
    
    Returns:
        List of (time, text) tuples
    """
    text_keys = []
    
    # Get strip parameters if provided
    frame_offset = 0
    scale = 1.0
    if strip:
        frame_offset = strip.frame_start - strip.action_frame_start
        scale = strip.scale
    
    # Process each pose marker
    for marker in action.pose_markers:
        # Apply strip transformations
        effective_frame = (marker.frame * scale) + frame_offset
        
        # Convert to time in seconds
        time = effective_frame / frame_rate
        
        # Add to list
        text_keys.append((time, marker.name))
    
    # Sort by time
    text_keys.sort(key=lambda x: x[0])
    
    return text_keys
```

### Writing Text Keys to NIF

```python
def write_text_keys_to_nif(nif_file, text_keys_list):
    """
    Create/update NiTextKeyExtraData in NIF file.
    
    Args:
        nif_file: NIFFile object
        text_keys_list: List of (time, text) tuples
    """
    # Create NiTextKeyExtraData block
    extra_data = nif_file.create_block("NiTextKeyExtraData")
    
    # Add each text key
    for time, text in sorted(text_keys_list, key=lambda x: x[0]):
        key = extra_data.keys.add()
        key.time = float(time)
        key.text = text
    
    # Attach to root node
    root = nif_file.get_root()
    root.extra_data = extra_data
```

## Best Practices

1. **Frame Alignment**: Place pose markers at clear animation frame boundaries to avoid floating-point precision issues.

2. **Naming Consistency**: Use exact animation group names (case-sensitive) defined in your game data.

3. **Non-overlapping Events**: Ensure start/stop markers don't overlap; the animation engine expects `start` before `stop`.

4. **Sound IDs**: Verify sound IDs reference existing game audio files.

5. **NLA Strip Management**: Document any frame offset/scaling applied to strips, as this affects exported time values.

6. **Looping Definitions**: For loopable animations, define both `{group}: loop start` and `{group}: loop stop` markers.

## Performance Considerations

- Text key lookups use binary search (O(log n)) via `TextKeyMap::lowerBound()`
- Animation playback iterates only keys within the current animation's time range
- No dynamic allocation of text keys during playback—all data loaded at asset import time

## References

- OpenMW Animation System: `apps/openmw/mwrender/animation.cpp`
- Text Key Storage: `components/sceneutil/textkeymap.hpp`
- NIF Extra Data: `components/nif/extra.hpp`, `components/nif/extra.cpp`
- COLLADA Export Documentation: https://github.com/openmw/collada-exporter
- Example Assets: https://gitlab.com/OpenMW/example-suite/-/tree/master/example_animated_creature
