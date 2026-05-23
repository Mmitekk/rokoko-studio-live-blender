import bpy
import copy
import math
import mathutils
import os

from . import detector
from ..core import utils
from ..core.retargeting import get_source_armature, get_target_armature
from ..core import detection_manager as detector
from ..core import custom_schemes_manager
from ..panels.retargeting import BoneListItem

RETARGET_ID = '_RSL_RETARGET'

# Common bone name patterns for hip/root motion bones, used for auto-detection
HIP_BONE_PATTERNS = [
    'hip', 'hips', 'pelvis', 'root', 'rootmotion',
    'bip01_pelvis', 'bip_pelvis',
    'cc_base_pelvis', 'cc_base_hip',
    'hlp_root', 'def_hips',
    'mixamorig:hips',  # Mixamo skeleton
]

# Detection keys that indicate a hip/root motion bone
HIP_DETECTION_KEYS = ['hip', 'hips', 'pelvis']


class BuildBoneList(bpy.types.Operator):
    bl_idname = "rsl.build_bone_list"
    bl_label = "Build Bone List"
    bl_description = "Builds the bone list from the animation and tries to automatically detect and match bones"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    def execute(self, context):
        armature_source = get_source_armature()
        armature_target = get_target_armature()

        if not armature_source.animation_data or not armature_source.animation_data.action:
            self.report({'ERROR'}, 'No animation on the source armature found!'
                                   '\nSelect an armature with an animation as source.')
            return {'CANCELLED'}

        if armature_source.name == armature_target.name:
            self.report({'ERROR'}, 'Source and target armature are the same!'
                                   '\nPlease select different armatures.')
            return {'CANCELLED'}

        retargeting_dict = detector.detect_retarget_bones()

        # Clear the bone retargeting list
        context.scene.rsl_retargeting_bone_list.clear()

        for bone_source, bone_values in retargeting_dict.items():
            bone_target, bone_key = bone_values

            bone_item = context.scene.rsl_retargeting_bone_list.add()
            bone_item.bone_name_key = bone_key
            bone_item.bone_name_source = bone_source
            bone_item.bone_name_target = bone_target

        # Auto-detect root motion bones if in AUTO mode
        if context.scene.rsl_retargeting_root_motion_mode == 'AUTO':
            root_source, root_target = self.auto_detect_root_motion_bones(context)
            if root_source and root_target:
                context.scene.rsl_retargeting_root_bone_source = root_source
                context.scene.rsl_retargeting_root_bone_target = root_target

        return {'FINISHED'}

    def auto_detect_root_motion_bones(self, context):
        """Try to automatically find the hip/root motion bone in source and target armatures."""
        armature_source = get_source_armature()
        armature_target = get_target_armature()

        # Find the source hip bone from the bone list
        root_source = ''
        root_target = ''
        for item in context.scene.rsl_retargeting_bone_list:
            bone_key_lower = item.bone_name_key.lower()
            source_lower = item.bone_name_source.lower()
            if any(pattern in bone_key_lower or pattern in source_lower for pattern in ['hip', 'pelvis']):
                if item.bone_name_target:  # Only if target bone was found
                    root_source = item.bone_name_source
                    root_target = item.bone_name_target
                    break

        # Fallback: search by bone name patterns directly in armatures
        if not root_source:
            root_source = self.find_hip_bone_by_name(armature_source)
        if not root_target:
            root_target = self.find_hip_bone_by_name(armature_target)

        return root_source, root_target

    def find_hip_bone_by_name(self, armature):
        """Find a hip-like bone by checking its name against common patterns."""
        if not armature:
            return ''
        for bone in armature.pose.bones:
            bone_name_lower = bone.name.lower().replace('_', '').replace(' ', '')
            for pattern in HIP_BONE_PATTERNS:
                pattern_clean = pattern.replace('_', '').replace(' ', '')
                if pattern_clean in bone_name_lower:
                    return bone.name
        return ''


class AddBoneListItem(bpy.types.Operator):
    bl_idname = "rsl.add_bone_list_item"
    bl_label = "Add Bone List Item"
    bl_description = "Adds a customizable bone list item"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    def execute(self, context):
        bone_item = context.scene.rsl_retargeting_bone_list.add()
        bone_item.is_custom = True

        context.scene.rsl_retargeting_bone_list_index = len(context.scene.rsl_retargeting_bone_list) - 1
        return {'FINISHED'}


class ClearBoneList(bpy.types.Operator):
    bl_idname = "rsl.clear_bone_list"
    bl_label = "Clear Bone List"
    bl_description = "Clears the bone list so that you can manually fill in all bones"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    def execute(self, context):
        for bone_item in context.scene.rsl_retargeting_bone_list:
            bone_item.bone_name_target = ''
        return {'FINISHED'}


class PrepareForUE5(bpy.types.Operator):
    """Prepare the retargeted armature for Unreal Engine FBX export.

    Applies the armature's object-level rotation, scale, and location to the
    bone data, then resets the object transforms to identity.  Also removes
    any leftover object-level animation keyframes so UE5 does not double-apply
    the transform.

    After running this, use File > Export > FBX with these settings:
      Forward: -Z Forward | Up: Y Up | Scale: 1.0 | Apply Scalings: FBX All
    Or simply use the "Export FBX for UE5" button which does everything
    automatically.
    """
    bl_idname = "rsl.prepare_for_ue5"
    bl_label = "Prepare for UE5 Export"
    bl_description = ('Applies object transforms to bone data and removes '
                      'object-level animation. Fixes flipped/oversized import in UE5. '
                      'After this, export FBX with Forward=-Z, Up=Y.')
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'ARMATURE'

    def execute(self, context):
        armature = context.active_object

        if not armature or armature.type != 'ARMATURE':
            self.report({'ERROR'}, 'Select an armature object first!')
            return {'CANCELLED'}

        changes = []

        # Apply all object transforms to bone data
        utils.set_active(armature)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        changes.append('Transforms applied')

        # Remove object-level animation fcurves (location/rotation/scale)
        # After applying transforms these would conflict with the baked bone data
        if armature.animation_data and armature.animation_data.action:
            fcurves_to_remove = []
            for fcurve in armature.animation_data.action.fcurves:
                if fcurve.data_path in ('location', 'rotation_euler', 'rotation_quaternion',
                                        'rotation_axis_angle', 'scale'):
                    fcurves_to_remove.append(fcurve)
            for fcurve in fcurves_to_remove:
                armature.animation_data.action.fcurves.remove(fcurve)
            if fcurves_to_remove:
                changes.append(f'{len(fcurves_to_remove)} object fcurves removed')

        if changes:
            self.report({'INFO'}, f'UE5 prep: {", ".join(changes)}. '
                                  'Now export FBX: Forward=-Z, Up=Y, Scale=1.0')
        else:
            self.report({'INFO'}, 'Armature already prepared. Export FBX: Forward=-Z, Up=Y, Scale=1.0')

        return {'FINISHED'}


class ExportFBXForUE5(bpy.types.Operator):
    """Export the selected armature as FBX with correct settings for Unreal Engine 5.

    This operator automates the entire export workflow without modifying the
    original armature:
      1. Duplicates the armature + child meshes together
      2. Samples root bone world positions BEFORE transform_apply
      3. Applies all object transforms on the copies (bakes rotation/scale into bones)
      4. Corrects root bone location keyframes using sampled world positions
      5. Removes ALL object-level animation keyframes from the copy
      6. Exports as FBX with UE5-compatible settings (Forward=-Z, Up=Y)
      7. Deletes the temporary copies

    Root motion is kept ON THE BONE (not the armature object). The armature
    is exported as armature_nodetype='NULL', so UE5 treats the first real bone
    (e.g. mixamorig:Hips) as the skeleton root. This ensures both root motion
    extraction and Force Root Lock work correctly in UE5.
    """
    bl_idname = "rsl.export_fbx_ue5"
    bl_label = "Export FBX"
    bl_description = ('One-click export to UE5-ready FBX. Applies transforms on a '
                      'temporary copy, exports with Forward=-Z / Up=Y (armature as NULL), '
                      'and cleans up. Root motion stays on the bone for correct Force Root Lock.')
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filename_ext = ".fbx"
    filter_glob: bpy.props.StringProperty(default="*.fbx", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'ARMATURE'

    def invoke(self, context, event):
        if not self.filepath:
            armature = context.active_object
            blend_name = os.path.splitext(os.path.basename(bpy.data.filepath))[0] if bpy.data.filepath else 'untitled'
            arm_name = armature.name if armature else 'armature'
            self.filepath = f"{blend_name}_{arm_name}.fbx"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        armature = context.active_object

        if not armature or armature.type != 'ARMATURE':
            self.report({'ERROR'}, 'Select an armature object first!')
            return {'CANCELLED'}

        filepath = self.filepath
        if not filepath.lower().endswith('.fbx'):
            filepath += '.fbx'

        # --- Step 1: Select armature + child meshes and duplicate together ---
        bpy.ops.object.select_all(action='DESELECT')
        armature.select_set(True)
        child_meshes = []
        for child in armature.children:
            if child.type == 'MESH':
                child.select_set(True)
                child_meshes.append(child)

        # Duplicate all selected objects at once (preserves parent-child relationships)
        bpy.ops.object.duplicate_move(OBJECT_OT_duplicate={"linked": False})

        # Identify the duplicated objects
        armature_copy = context.active_object
        mesh_copies = [obj for obj in context.selected_objects if obj.type == 'MESH']

        # --- Step 2: Sample root bone world positions BEFORE transform_apply ---
        # We must sample the bone's actual world position at each frame BEFORE
        # applying any transforms, because transform_apply(rotation=True) changes
        # the bone rest poses but does NOT adjust animation keyframes. This means
        # bone.location values after transform_apply are in the old armature-local
        # space, not world space. By sampling before apply, we get correct world
        # positions that we can then use to set the armature's location animation.
        root_bone_world_positions = self._sample_root_bone_world_positions(armature_copy)

        # --- Step 3: Apply ALL transforms on the copies ---
        # This bakes the armature's rotation (e.g. -90 deg X from BVH import)
        # and scale into the bone data, which is what UE5 expects.
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature_copy)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        for mesh in mesh_copies:
            bpy.ops.object.select_all(action='DESELECT')
            utils.set_active(mesh)
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # --- Step 4: Correct root bone location after transform_apply ---
        # After transform_apply, the armature is at identity, but the bone's
        # location keyframes are in the OLD armature-local space and are now
        # WRONG. We must recompute them using the world positions sampled
        # BEFORE transform_apply.
        #
        # We keep the root motion ON THE BONE (not the armature object) and
        # export with armature_nodetype='NULL'. This way:
        #   - UE5 treats the armature as a helper node (not a bone)
        #   - UE5 uses the first real bone (e.g. mixamorig:Hips) as the root
        #   - Root motion extraction works from the real bone
        #   - Force Root Lock works correctly (no axis conversion rotation issues)
        self._correct_root_bone_location(armature_copy, root_bone_world_positions)

        # --- Step 5: Remove ALL object-level animation fcurves ---
        # After applying transforms, the armature object is at identity.
        # All object-level keyframes are stale. Root motion is on the BONE,
        # not the armature object, so we remove everything.
        if armature_copy.animation_data and armature_copy.animation_data.action:
            fcurves_to_remove = []
            for fcurve in armature_copy.animation_data.action.fcurves:
                if fcurve.data_path in ('location', 'rotation_euler', 'rotation_quaternion',
                                        'rotation_axis_angle', 'scale'):
                    fcurves_to_remove.append(fcurve)
            for fcurve in fcurves_to_remove:
                armature_copy.animation_data.action.fcurves.remove(fcurve)

        # Remove NLA tracks on copy (they can interfere with FBX bake)
        if armature_copy.animation_data:
            for track in list(armature_copy.animation_data.nla_tracks):
                armature_copy.animation_data.nla_tracks.remove(track)

        # Remove orphaned actions that could cause multiple animations in UE5.
        current_action = armature_copy.animation_data.action if armature_copy.animation_data else None
        for action in list(bpy.data.actions):
            try:
                if action == current_action:
                    continue
                real_users = action.users - (1 if action.use_fake_user else 0)
                if real_users <= 0:
                    action.use_fake_user = False
                    bpy.data.actions.remove(action)
            except ReferenceError:
                pass

        # --- Step 6: Select all copies for export ---
        bpy.ops.object.select_all(action='DESELECT')
        armature_copy.select_set(True)
        for mesh in mesh_copies:
            mesh.select_set(True)

        # --- Step 7: Export FBX with UE5 settings ---
        try:
            bpy.ops.export_scene.fbx(
                filepath=filepath,
                use_selection=True,
                global_scale=1.0,
                apply_scale_options='FBX_SCALE_ALL',
                axis_forward='-Z',
                axis_up='Y',
                use_mesh_modifiers=True,
                mesh_smooth_type='OFF',
                use_tspace=True,
                use_custom_props=False,
                bake_anim=True,
                bake_anim_use_all_bones=True,
                bake_anim_use_nla_strips=False,
                bake_anim_use_all_actions=False,
                bake_anim_force_startend_keying=True,
                bake_anim_step=1.0,
                bake_anim_simplify_factor=0.0,
                use_mesh_edges=False,
                use_mesh_vertices=False,
                primary_bone_axis='Y',
                secondary_bone_axis='X',
                armature_nodetype='NULL',
                bake_space_transform=False,
                object_types={'ARMATURE', 'MESH'},
            )
            print(f'RSL Export FBX for UE5: Exported to "{filepath}"')
        except TypeError:
            # Fallback for older Blender versions that may not support all params
            try:
                bpy.ops.export_scene.fbx(
                    filepath=filepath,
                    use_selection=True,
                    global_scale=1.0,
                    axis_forward='-Z',
                    axis_up='Y',
                    use_mesh_modifiers=True,
                    mesh_smooth_type='OFF',
                    use_tspace=True,
                    bake_anim=True,
                    bake_anim_use_all_bones=True,
                    bake_anim_use_nla_strips=False,
                    bake_anim_use_all_actions=False,
                    primary_bone_axis='Y',
                    secondary_bone_axis='X',
                    armature_nodetype='NULL',
                    object_types={'ARMATURE', 'MESH'},
                )
                print(f'RSL Export FBX (compat mode): Exported to "{filepath}"')
            except Exception as e:
                self.report({'ERROR'}, f'FBX export failed: {e}')
                self._cleanup(armature_copy, mesh_copies)
                return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f'FBX export failed: {e}')
            self._cleanup(armature_copy, mesh_copies)
            return {'CANCELLED'}

        # --- Step 8: Clean up - delete the temporary copies ---
        self._cleanup(armature_copy, mesh_copies)

        # Restore original selection
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature)
        armature.select_set(True)

        self.report({'INFO'}, f'FBX exported for UE5: {filepath}')
        return {'FINISHED'}

    @staticmethod
    def _sample_root_bone_world_positions(armature):
        """
        Sample the root bone's world position at every frame of the animation
        using depsgraph evaluation. Must be called BEFORE transform_apply.

        Returns: dict {frame: mathutils.Vector} or empty dict if no animation
        """
        if not armature.animation_data or not armature.animation_data.action:
            print('RSL Export: No animation data - cannot sample root bone positions')
            return {}

        action = armature.animation_data.action

        # Find the parentless root bone
        root_bone_name = ''
        for bone in armature.pose.bones:
            if not bone.parent:
                root_bone_name = bone.name
                break

        if not root_bone_name:
            print('RSL Export: No parentless root bone found - cannot sample')
            return {}

        # Get the animation frame range
        frame_start = None
        frame_end = None
        for fcurve in action.fcurves:
            for kp in fcurve.keyframe_points:
                f = kp.co.x
                if frame_start is None or f < frame_start:
                    frame_start = f
                if frame_end is None or f > frame_end:
                    frame_end = f

        if frame_start is None:
            return {}

        frame_start = int(frame_start)
        frame_end = int(frame_end)

        print(f'RSL Export: Sampling root bone "{root_bone_name}" world positions '
              f'(frames {frame_start}-{frame_end})')

        # Switch to object mode for depsgraph evaluation
        bpy.context.view_layer.objects.active = armature
        if armature.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Sample the bone's world position at each frame
        world_positions = {}
        for frame in range(frame_start, frame_end + 1):
            bpy.context.scene.frame_set(frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            armature_eval = armature.evaluated_get(depsgraph)
            root_bone_eval = armature_eval.pose.bones.get(root_bone_name)
            if root_bone_eval:
                # The bone's head position in world space
                world_pos = armature_eval.matrix_world @ root_bone_eval.head.copy()
                world_positions[frame] = world_pos
            else:
                # Fallback
                root_bone = armature.pose.bones[root_bone_name]
                world_pos = armature.matrix_world @ root_bone.head.copy()
                world_positions[frame] = world_pos

        print(f'RSL Export: Sampled {len(world_positions)} frames. '
              f'First: {world_positions[frame_start]}, '
              f'Last: {world_positions[frame_end]}')

        return world_positions

    @staticmethod
    def _correct_root_bone_location(armature, root_bone_world_positions):
        """
        Correct the root bone's location keyframes after transform_apply.

        After transform_apply(rotation=True, location=True, scale=True), the armature
        object is at identity transform. However, the bone's location keyframes are
        still in the OLD armature-local space and are now INCORRECT because the
        armature's rotation was baked into the bone rest poses.

        This method recomputes the root bone's location keyframes using the world
        positions sampled BEFORE transform_apply, so the bone's visual world position
        is preserved:
            bone.location = world_pos_sampled - bone.head_local_after_apply

        The root motion stays ON THE BONE (not transferred to armature object).
        With armature_nodetype='NULL', UE5 treats the armature as a helper node
        and uses the root bone (e.g. mixamorig:Hips) as the skeleton root.
        This ensures Force Root Lock works correctly in UE5.
        """
        if not root_bone_world_positions:
            print('RSL Export: No root bone world positions - skipping correction')
            return

        if not armature.animation_data or not armature.animation_data.action:
            print('RSL Export: No animation data - skipping correction')
            return

        action = armature.animation_data.action

        # Find the parentless root bone
        root_bone_name = ''
        for bone in armature.pose.bones:
            if not bone.parent:
                root_bone_name = bone.name
                break

        if not root_bone_name:
            print('RSL Export: No parentless root bone found - skipping correction')
            return

        root_bone = armature.pose.bones[root_bone_name]

        # After transform_apply(all), armature is at origin with identity rotation.
        # bone.head_local is the rest head position in the new armature space.
        bone_rest_head = root_bone.bone.head_local.copy()

        print(f'RSL Export: Root bone "{root_bone_name}" rest head after apply: {bone_rest_head}')

        # Compute corrected bone location at each frame:
        # bone.location = world_pos - bone_rest_head
        # This preserves the visual position because:
        #   new_world_pos = armature.matrix_world @ (bone_rest_head + bone.location)
        #                = identity @ (bone_rest_head + (world_pos - bone_rest_head))
        #                = world_pos  ✓
        bone_locations = {}
        for frame, world_pos in root_bone_world_positions.items():
            bone_locations[frame] = world_pos - bone_rest_head

        # Debug: show the displacement
        frames = sorted(bone_locations.keys())
        if len(frames) > 1:
            first_loc = bone_locations[frames[0]]
            last_loc = bone_locations[frames[-1]]
            delta = last_loc - first_loc
            print(f'RSL Export: Root bone location delta ({frames[0]}→{frames[-1]}): {delta}')
            print(f'RSL Export: Delta length: {delta.length:.4f}')

        # Remove existing bone location fcurves and recreate with correct values
        root_loc_fcurves = []
        for fcurve in action.fcurves:
            if fcurve.data_path == f'pose.bones["{root_bone_name}"].location':
                root_loc_fcurves.append(fcurve)

        for fcurve in root_loc_fcurves:
            action.fcurves.remove(fcurve)

        # Create new bone location fcurves with corrected values
        loc_fcurves = {}
        for axis in range(3):
            fc = action.fcurves.new(
                data_path=f'pose.bones["{root_bone_name}"].location',
                index=axis)
            loc_fcurves[axis] = fc

        # Populate bone location keyframes
        for frame in sorted(bone_locations.keys()):
            loc = bone_locations[frame]
            for axis in range(3):
                fc = loc_fcurves[axis]
                fc.keyframe_points.insert(frame, loc[axis])

        # Update fcurve handles
        for axis, fc in loc_fcurves.items():
            fc.update()

        # Set the bone's current location to the first frame value
        if frames:
            root_bone.location = bone_locations[frames[0]]

        # Verify
        max_displacement = 0.0
        if len(frames) > 1:
            first_loc = bone_locations[frames[0]]
            last_loc = bone_locations[frames[-1]]
            max_displacement = (last_loc - first_loc).length

        print(f'RSL Export: Root bone location corrected '
              f'({len(bone_locations)} keyframes, displacement={max_displacement:.4f})')
        print(f'RSL Export: Bone location at frame {frames[0]}: {bone_locations[frames[0]]}')
        print(f'RSL Export: Bone location at frame {frames[-1]}: {bone_locations[frames[-1]]}')

    @staticmethod
    def _cleanup(armature_copy, mesh_copies):
        """Delete the temporary duplicate armature and meshes."""
        bpy.ops.object.select_all(action='DESELECT')
        armature_copy.select_set(True)
        for mesh in mesh_copies:
            mesh.select_set(True)
        bpy.ops.object.delete()

class ApplyTPoseReference(bpy.types.Operator):
    """Apply T-pose rotations from a reference armature to the source animation's first frame."""
    bl_idname = "rsl.apply_tpose_reference"
    bl_label = "Apply T-Pose to Animation"
    bl_description = ('Copy T-pose rotations from the reference armature to the source animation on frame 1. '
                      'This fixes issues where imported BVH animations have a rest pose that differs from T-pose '
                      '(e.g. A-pose), which causes arms to freeze or not animate correctly during retargeting.')
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        tpose_ref = context.scene.rsl_retargeting_tpose_reference
        armature_source = get_source_armature()
        return (tpose_ref and tpose_ref.type == 'ARMATURE'
                and armature_source and armature_source.type == 'ARMATURE'
                and armature_source.animation_data and armature_source.animation_data.action)

    def execute(self, context):
        tpose_ref = context.scene.rsl_retargeting_tpose_reference
        armature_source = get_source_armature()

        if not tpose_ref or tpose_ref.type != 'ARMATURE':
            self.report({'ERROR'}, 'T-Pose reference armature not found or not an armature!')
            return {'CANCELLED'}

        if not armature_source or armature_source.type != 'ARMATURE':
            self.report({'ERROR'}, 'Source armature not found or not an armature!')
            return {'CANCELLED'}

        if not armature_source.animation_data or not armature_source.animation_data.action:
            self.report({'ERROR'}, 'Source armature has no animation data!')
            return {'CANCELLED'}

        count = self.apply_tpose(tpose_ref, armature_source)

        self.report({'INFO'}, f'T-Pose applied: {count} bone rotations copied to frame 1.')
        return {'FINISHED'}

    @staticmethod
    def apply_tpose(tpose_ref, armature_source, frame=1):
        """
        Apply T-pose rotations from a reference armature to the source animation at the given frame.
        """
        # Go to the target frame
        bpy.context.scene.frame_set(frame)

        # 1. Read rotations from the T-pose reference armature
        src_rots = {}
        for b in tpose_ref.pose.bones:
            if b.rotation_mode == 'QUATERNION':
                src_rots[b.name] = ('QUATERNION', tuple(b.rotation_quaternion))
            else:
                src_rots[b.name] = ('EULER', tuple(b.rotation_euler))

        # 2. Switch to the source armature in pose mode
        bpy.context.view_layer.objects.active = armature_source
        if armature_source.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='POSE')

        # 3. Remove old rotation keyframes at the target frame from the source action
        act = armature_source.animation_data.action if armature_source.animation_data else None
        if act:
            for fc in act.fcurves:
                if "rotation_euler" in fc.data_path or "rotation_quaternion" in fc.data_path:
                    pts = fc.keyframe_points
                    for i in range(len(pts) - 1, -1, -1):
                        if abs(pts[i].co.x - float(frame)) < 0.5:
                            pts.remove(pts[i], fast=True)

        # 4. Apply T-pose rotations and insert keyframes
        count = 0
        for name, (rot_mode, rot) in src_rots.items():
            if name not in armature_source.pose.bones:
                continue

            bone = armature_source.pose.bones[name]

            if rot_mode == 'QUATERNION':
                bone.rotation_quaternion = rot
                bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)
            else:
                bone.rotation_euler = rot
                bone.keyframe_insert(data_path="rotation_euler", frame=frame)

            count += 1

        bpy.ops.object.mode_set(mode='OBJECT')
        return count


class RetargetAnimation(bpy.types.Operator):
    bl_idname = "rsl.retarget_animation"
    bl_label = "Retarget Animation"
    bl_description = "Retargets the animation from the source armature to the target armature"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    retarget_bone_list: [BoneListItem] = []

    def execute(self, context):
        armature_source = get_source_armature()
        armature_target = get_target_armature()

        if not armature_source.animation_data or not armature_source.animation_data.action:
            self.report({'ERROR'}, 'No animation on the source armature found!'
                                   '\nSelect an armature with an animation as source.')
            return {'CANCELLED'}

        if armature_source.name == armature_target.name:
            self.report({'ERROR'}, 'Source and target armature are the same!'
                                   '\nPlease select different armatures.')
            return {'CANCELLED'}

        # Build retargeting bone list
        self.retarget_bone_list.clear()
        for item in context.scene.rsl_retargeting_bone_list:
            if not item.bone_name_source or not item.bone_name_target \
                    or not armature_source.pose.bones.get(item.bone_name_source) \
                    or not armature_target.pose.bones.get(item.bone_name_target):
                continue
            self.retarget_bone_list.append(item)

        # Find the root bones (parentless) and cancel if none are found
        root_bones = self.find_root_bones(context, armature_source, armature_target)
        if not root_bones:
            self.report({'ERROR'}, 'No root bone found!'
                                   '\nCheck if the bones are mapped correctly or try rebuilding the bone list.')
            return {'CANCELLED'}

        print(f'RSL: Parentless root bones found: {root_bones}')

        # --- Root Motion Logic ---
        root_motion_mode = context.scene.rsl_retargeting_root_motion_mode
        root_motion_bones = {}  # dict: target_bone_name -> source_bone_name

        if root_motion_mode != 'OFF':
            if root_motion_mode == 'AUTO':
                rm_source, rm_target = self.find_root_motion_bones_auto(
                    armature_source, armature_target, root_bones)
            else:  # CUSTOM
                rm_source = context.scene.rsl_retargeting_root_bone_source
                rm_target = context.scene.rsl_retargeting_root_bone_target

                if rm_source and not armature_source.pose.bones.get(rm_source):
                    self.report({'ERROR'}, f'Source root motion bone "{rm_source}" not found in source armature!')
                    return {'CANCELLED'}
                if rm_target and not armature_target.pose.bones.get(rm_target):
                    self.report({'ERROR'}, f'Target root motion bone "{rm_target}" not found in target armature!')
                    return {'CANCELLED'}

            if rm_source and rm_target:
                root_motion_bones[rm_target] = rm_source
                print(f'RSL Root Motion: Source="{rm_source}", Target="{rm_target}"')
            elif root_motion_mode == 'CUSTOM' and (not rm_source or not rm_target):
                self.report({'WARNING'}, 'Root motion bone not specified. Retargeting without root motion.')
            else:
                print('RSL Root Motion: Auto-detection did not find a hip bone.')

        # Merge root_motion_bones into root_bones for location baking purposes
        root_bones_with_motion = list(root_bones)
        for rm_target in root_motion_bones:
            if rm_target not in root_bones_with_motion:
                root_bones_with_motion.append(rm_target)

        # IMPORTANT: Also include the PARENTLESS root bone in root_bones_with_motion,
        # even if it's not in the retarget list. In UE5 skeletons like the mannequin,
        # the hierarchy is Root → Pelvis. "Root" often has no matching source bone so
        # it's not in the retarget list. But we MUST include it so that:
        #   1. It gets selected for baking (otherwise it has no keyframes at all)
        #   2. Its location keyframes are preserved during bake
        #   3. After bake, extract_root_motion_to_root_bone() can put walking motion on it
        for bone in armature_target.pose.bones:
            if not bone.parent and bone.name not in root_bones_with_motion:
                root_bones_with_motion.append(bone.name)
                print(f'RSL: Added parentless root bone "{bone.name}" to root_bones_with_motion '
                      f'(was not in retarget list)')

        print(f'RSL: Root bones with motion: {root_bones_with_motion}')

        # Check for duplicate target bone entries
        seen = {}
        for item in self.retarget_bone_list:
            count = seen.get(item.bone_name_target)
            if not count:
                count = 0
            seen[item.bone_name_target] = count + 1
        duplicates = [key for key, value in seen.items() if value > 1]
        if duplicates:
            self.report({'ERROR'}, 'Duplicate target bone entries found! Please use each target bone only once:'
                                   f'\n{", ".join(duplicates)}')
            return {'CANCELLED'}

        # Save the bone list if the user changed anything
        custom_schemes_manager.save_retargeting_to_list()

        # --- T-Pose Reference: Apply before retargeting ---
        tpose_applied = False
        tpose_ref = context.scene.rsl_retargeting_tpose_reference
        if tpose_ref and context.scene.rsl_retargeting_tpose_apply_before:
            if (tpose_ref.type == 'ARMATURE' and
                    armature_source.animation_data and armature_source.animation_data.action):
                count = ApplyTPoseReference.apply_tpose(tpose_ref, armature_source, frame=1)
                tpose_applied = True
                print(f'RSL: T-Pose reference applied before retargeting ({count} bones)')

        # --- KEY FIX: Transfer armature-level location data to hip bone ---
        # Many BVH imports store root motion as the armature OBJECT's location,
        # not as a bone's location. This data would be lost during clean_animation().
        # We transfer it to the hip bone BEFORE any cleanup so it's preserved.
        self.transfer_armature_location_to_hip(armature_source, root_motion_bones)

        # --- After transferring, remove the armature-level location fcurves ---
        # This prevents double-movement (armature location + bone location both moving)
        # during the retargeting process. The data is now safely on the hip bone.
        if armature_source.animation_data and armature_source.animation_data.action:
            fcurves_to_remove = []
            for fcurve in armature_source.animation_data.action.fcurves:
                if fcurve.data_path == 'location':
                    fcurves_to_remove.append(fcurve)
            for fcurve in fcurves_to_remove:
                armature_source.animation_data.action.fcurves.remove(fcurve)
                print(f'RSL: Removed armature-level location fcurve (axis {fcurve.array_index})')

        # --- Clean up previous retargeting data on the target armature ---
        # Multiple retargeting runs can leave NLA tracks and orphaned actions
        # which cause the FBX exporter to include multiple animations.
        if armature_target.animation_data:
            for track in list(armature_target.animation_data.nla_tracks):
                armature_target.animation_data.nla_tracks.remove(track)
                print(f'RSL: Removed old NLA track from target armature')

        # Remove ALL orphaned actions (including those kept alive by fake_user)
        # from previous retargeting runs. This is critical to prevent the FBX
        # exporter from including stale animations.
        for action in list(bpy.data.actions):
            try:
                real_users = action.users - (1 if action.use_fake_user else 0)
                if real_users <= 0:
                    name = action.name  # Save name before removal
                    action.use_fake_user = False
                    bpy.data.actions.remove(action)
                    print(f'RSL: Removed orphaned action "{name}"')
            except ReferenceError:
                pass  # Action was already removed by Blender

        # Prepare armatures
        utils.set_active(armature_target)
        bpy.ops.object.mode_set(mode='OBJECT')
        utils.set_active(armature_source)
        bpy.ops.object.mode_set(mode='OBJECT')

        # Set armatures into pose mode
        armature_source.data.pose_position = 'POSE'
        armature_target.data.pose_position = 'POSE'

        # Save and reset the current pose position of both armatures if rest position should be used
        pose_source, pose_target = {}, {}
        if bpy.context.scene.rsl_retargeting_use_pose == 'REST':
            pose_source = self.get_and_reset_pose_rotations(armature_source)
            pose_target = self.get_and_reset_pose_rotations(armature_target)

        # Auto scaling
        source_scale = None
        if context.scene.rsl_retargeting_auto_scaling:
            # Clean source animation (ONLY rotation/scale at armature level,
            # NOT location - location was already transferred to hip bone above)
            self.clean_animation(armature_source)

            # Scale the source armature to fit the target armature
            source_scale = copy.deepcopy(armature_source.scale)
            self.scale_armature(context, armature_source, armature_target, root_bones_with_motion)

        # --- Root Motion: Save rest pose head positions for offset calculation ---
        root_motion_rest_offsets = {}
        if root_motion_bones and context.scene.rsl_retargeting_root_motion_keep_offset:
            for rm_target, rm_source in root_motion_bones.items():
                bone_src = armature_source.pose.bones.get(rm_source)
                bone_tgt = armature_target.pose.bones.get(rm_target)
                if bone_src and bone_tgt:
                    src_head_ws = armature_source.matrix_world @ bone_src.head.copy()
                    tgt_head_ws = armature_target.matrix_world @ bone_tgt.head.copy()
                    root_motion_rest_offsets[rm_target] = {
                        'source_head_ws': src_head_ws,
                        'target_head_ws': tgt_head_ws,
                        'offset': tgt_head_ws - src_head_ws,
                    }

        # Duplicate source armature to apply transforms to the animation
        armature_source_original = armature_source
        armature_source = self.copy_rest_pose(context, armature_source)

        # Save transforms of target armature
        rotation_mode = armature_target.rotation_mode
        armature_target.rotation_mode = 'QUATERNION'
        rotation = copy.deepcopy(armature_target.rotation_quaternion)
        location = copy.deepcopy(armature_target.location)

        # Apply transforms of the target armature
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature_target)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        bpy.ops.object.mode_set(mode='EDIT')

        # Create a transformation dict of all bones of the target armature and unselect all bones
        bone_transforms = {}
        for bone in context.object.data.edit_bones:
            bone.select = False
            bone_transforms[bone.name] = armature_source.matrix_world.inverted() @ bone.head.copy(), \
                                         armature_source.matrix_world.inverted() @ bone.tail.copy(), \
                                         utils.mat3_to_vec_roll(armature_source.matrix_world.inverted().to_3x3() @ bone.matrix.to_3x3())

        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature_source)
        bpy.ops.object.mode_set(mode='EDIT')

        # Recreate bones from target armature in source armature
        for item in self.retarget_bone_list:
            bone_source = armature_source.data.edit_bones.get(item.bone_name_source)

            bone_new = armature_source.data.edit_bones.new(item.bone_name_target + RETARGET_ID)
            bone_new.head, bone_new.tail, bone_new.roll = bone_transforms[item.bone_name_target]
            bone_new.parent = bone_source

        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')

        # Add constraints to target armature and select the bones for animation
        for item in self.retarget_bone_list:
            bone_target = armature_target.pose.bones.get(item.bone_name_target)

            # Add COPY_ROTATION constraint
            constraint = bone_target.constraints.new('COPY_ROTATION')
            constraint.name += RETARGET_ID
            constraint.target = armature_source
            constraint.subtarget = item.bone_name_target + RETARGET_ID

            # Add COPY_LOCATION for parentless root bones (original behavior)
            if bone_target.name in root_bones:
                constraint = bone_target.constraints.new('COPY_LOCATION')
                constraint.name += RETARGET_ID
                constraint.target = armature_source
                constraint.subtarget = item.bone_name_source
                # Explicitly set spaces for reliable location copying
                constraint.target_space = 'WORLD'
                constraint.owner_space = 'WORLD'
                print(f'RSL: COPY_LOCATION added for root bone "{bone_target.name}" -> source "{item.bone_name_source}" (WORLD/WORLD)')

            # Add COPY_LOCATION for root motion bones (e.g., hip bone with a parent)
            elif bone_target.name in root_motion_bones:
                rm_source_name = root_motion_bones[bone_target.name]
                constraint = bone_target.constraints.new('COPY_LOCATION')
                constraint.name += RETARGET_ID
                constraint.target = armature_source
                constraint.subtarget = rm_source_name
                # CRITICAL: Use WORLD space for both to correctly transfer position
                # regardless of parent bone orientation differences
                constraint.target_space = 'WORLD'
                constraint.owner_space = 'WORLD'
                print(f'RSL: COPY_LOCATION added for root motion bone "{bone_target.name}" -> source "{rm_source_name}" (WORLD/WORLD)')

            # Select the bone for animation
            armature_target.data.bones.get(item.bone_name_target).select = True

        # Also select the parentless ROOT bone for baking, even if it's not
        # in the retarget list. Without selection, it won't get baked and
        # extract_root_motion_to_root_bone() can't add location keyframes to it.
        for bone in armature_target.pose.bones:
            if not bone.parent:
                armature_target.data.bones.get(bone.name).select = True

        # Bake the animation to the target armature
        # IMPORTANT: Pass root_bones_with_motion (which includes hip/root motion bones)
        # NOT just root_bones (parentless only), otherwise hip location keyframes are stripped
        self.bake_animation(armature_source, armature_target, root_bones_with_motion)

        # --- Root Motion: Post-bake processing ---
        # After baking, UE5 needs the walking motion on the PARENTLESS ROOT bone.
        # The motion is typically on the HIPS bone (from COPY_LOCATION constraint).
        # process_root_motion_after_bake() ALWAYS extracts from HIPS to ROOT,
        # which is mathematically correct in all cases:
        #   delta = HIPS_current_world - HIPS_rest_world
        #   ROOT.location = delta, HIPS.location = 0
        #   → HIPS world position is preserved, ROOT carries the walking motion
        if root_motion_bones:
            self.process_root_motion_after_bake(
                armature_target, root_motion_bones, root_bones,
                context.scene.rsl_retargeting_root_motion_keep_offset,
                root_motion_rest_offsets)

        # Delete the duplicate helper armature
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature_source)
        bpy.data.actions.remove(armature_source.animation_data.action)
        bpy.ops.object.delete()

        # Change armature source back to original
        armature_source = armature_source_original

        # Change action name
        armature_target.animation_data.action.name = armature_source.animation_data.action.name + ' Retarget'

        # Final cleanup: remove orphaned actions that are no longer used.
        # This prevents the FBX exporter from including stale animations
        # (which was causing UE5 to import 7 animations instead of 1).
        # We must also remove actions with use_fake_user=True that have no
        # REAL users (i.e. they're only alive because of the fake_user flag).
        # These accumulate from repeated retargets and NLA bakes.
        for action in list(bpy.data.actions):
            try:
                # Count real users (subtract 1 for fake_user if set)
                real_users = action.users - (1 if action.use_fake_user else 0)
                if real_users <= 0:
                    action.use_fake_user = False
                    bpy.data.actions.remove(action)
            except ReferenceError:
                pass  # Action was already removed by Blender

        # Remove constraints from target armature
        for bone in armature_target.pose.bones:
            for constraint in bone.constraints:
                if RETARGET_ID in constraint.name:
                    bone.constraints.remove(constraint)

        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature_target)

        # Reset target armature transforms to old state
        armature_target.rotation_quaternion = rotation
        armature_target.location = location

        armature_target.rotation_quaternion.w = -armature_target.rotation_quaternion.w
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        armature_target.rotation_quaternion = rotation
        armature_target.rotation_mode = rotation_mode

        # Reset source armature scale
        if source_scale:
            armature_source.scale = source_scale

        bpy.ops.object.select_all(action='DESELECT')

        # Report result
        parts = []
        if tpose_applied:
            parts.append('T-pose applied')
        if root_motion_bones:
            rm_names = ', '.join(root_motion_bones.keys())
            parts.append(f'root motion on: {rm_names}')
        if parts:
            self.report({'INFO'}, 'Retargeted animation (' + '; '.join(parts) + ').')
        else:
            self.report({'INFO'}, 'Retargeted animation.')
        return {'FINISHED'}

    def find_root_bones(self, context, armature_source, armature_target):
        # Find all root bones (parentless bones)
        root_bones = []
        for bone in armature_target.pose.bones:
            if not bone.parent:
                root_bones.append(bone)

        # Find animated root bones
        root_bones_animated = []
        target_bones = [item.bone_name_target for item in self.retarget_bone_list]
        while root_bones:
            for bone in copy.copy(root_bones):
                root_bones.remove(bone)
                if bone.name in target_bones:
                    root_bones_animated.append(bone.name)
                else:
                    for bone_child in bone.children:
                        root_bones.append(bone_child)
        return root_bones_animated

    def find_root_motion_bones_auto(self, armature_source, armature_target, root_bones):
        """
        Automatically detect the hip/root motion bone pair.
        Returns (source_bone_name, target_bone_name) or ('', '') if not found.
        """
        # Strategy 1: Check the bone list for hip-like bones by their detection key
        for item in self.retarget_bone_list:
            if not item.bone_name_target:
                continue
            bone_key_lower = item.bone_name_key.lower()
            if bone_key_lower in HIP_DETECTION_KEYS:
                print(f'RSL Root Motion Auto: Found by detection key "{bone_key_lower}": '
                      f'source="{item.bone_name_source}", target="{item.bone_name_target}"')
                return item.bone_name_source, item.bone_name_target

        # Strategy 2: Check source AND target bone names against common hip patterns
        for item in self.retarget_bone_list:
            if not item.bone_name_target:
                continue
            # Check source bone name
            source_lower = item.bone_name_source.lower().replace('_', '').replace(' ', '')
            for pattern in HIP_BONE_PATTERNS:
                pattern_clean = pattern.replace('_', '').replace(' ', '')
                if pattern_clean in source_lower:
                    print(f'RSL Root Motion Auto: Found by source name pattern "{pattern}": '
                          f'source="{item.bone_name_source}", target="{item.bone_name_target}"')
                    return item.bone_name_source, item.bone_name_target

            # Check target bone name (e.g. "mixamorig:Hips")
            target_lower = item.bone_name_target.lower().replace('_', '').replace(' ', '')
            for pattern in HIP_BONE_PATTERNS:
                pattern_clean = pattern.replace('_', '').replace(' ', '')
                if pattern_clean in target_lower:
                    print(f'RSL Root Motion Auto: Found by target name pattern "{pattern}": '
                          f'source="{item.bone_name_source}", target="{item.bone_name_target}"')
                    return item.bone_name_source, item.bone_name_target

        # Strategy 3: Look for animated location data on bones in the source action
        bone_location_counts = {}
        if armature_source.animation_data and armature_source.animation_data.action:
            for fcurve in armature_source.animation_data.action.fcurves:
                if 'location' in fcurve.data_path:
                    bone_name = fcurve.data_path.split('"')
                    if len(bone_name) == 3:
                        name = bone_name[1]
                        if name not in bone_location_counts:
                            bone_location_counts[name] = 0
                        bone_location_counts[name] += len(fcurve.keyframe_points)

        if bone_location_counts:
            sorted_bones = sorted(bone_location_counts.items(), key=lambda x: x[1], reverse=True)
            for bone_name, count in sorted_bones:
                for item in self.retarget_bone_list:
                    if item.bone_name_source == bone_name and item.bone_name_target:
                        if bone_name not in root_bones:
                            print(f'RSL Root Motion Auto: Found by location data: {bone_name} ({count} keyframes)')
                            return item.bone_name_source, item.bone_name_target

        print('RSL Root Motion Auto: No hip bone found by any strategy')
        return '', ''

    def transfer_armature_location_to_hip(self, armature_source, root_motion_bones):
        """
        Transfer armature-object-level location keyframes to the hip bone.

        When BVH files are imported into Blender, the root motion (forward walking etc.)
        is often stored as the armature OBJECT's location animation, not as a bone's
        location. The original plugin's clean_animation() method deletes these object-level
        location fcurves, which destroys the root motion data entirely.

        This method detects if the armature object has location keyframes, and if so,
        transfers them to the hip/root motion bone BEFORE any cleanup happens.
        This ensures root motion survives the retargeting process.

        IMPORTANT: The armature location is in world space, but the bone location is in
        its parent's local space. We must correctly convert between these spaces.
        """
        if not armature_source.animation_data or not armature_source.animation_data.action:
            return

        action = armature_source.animation_data.action

        # Check if the armature OBJECT has location keyframes (data_path == 'location')
        obj_location_fcurves = []
        for fcurve in action.fcurves:
            if fcurve.data_path == 'location':
                obj_location_fcurves.append(fcurve)

        if not obj_location_fcurves:
            print('RSL: No armature-level location keyframes found (root motion is on bones already)')
            return

        print(f'RSL: Found {len(obj_location_fcurves)} armature-level location fcurves - '
              f'transferring to hip bone...')

        # Find the hip bone to transfer location data to
        hip_bone_name = ''
        if root_motion_bones:
            # Use the first root motion bone's source name
            hip_bone_name = list(root_motion_bones.values())[0]
        else:
            # Try to find hip bone by name
            hip_bone_name = self.find_hip_bone_by_name(armature_source)

        if not hip_bone_name or not armature_source.pose.bones.get(hip_bone_name):
            print('RSL WARNING: Could not find hip bone for armature-location transfer!')
            return

        hip_bone = armature_source.pose.bones[hip_bone_name]
        print(f'RSL: Transferring armature location to bone "{hip_bone_name}"')

        # Read all armature-level location keyframes
        location_data = {}  # axis -> [(frame, value)]
        for fcurve in obj_location_fcurves:
            axis = fcurve.array_index
            if axis not in location_data:
                location_data[axis] = []
            for kp in fcurve.keyframe_points:
                location_data[axis].append((kp.co.x, kp.co.y))

        # Collect all unique frames from the armature location animation
        all_frames = set()
        for axis, keyframes in location_data.items():
            for frame, value in keyframes:
                all_frames.add(int(round(frame)))
        all_frames = sorted(all_frames)

        if not all_frames:
            return

        # Switch to pose mode on the source armature
        bpy.context.view_layer.objects.active = armature_source
        if armature_source.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='POSE')

        # For CORRECT space conversion, we need to:
        # 1. Sample the armature's world-space location at each frame
        # 2. Convert that to the bone's parent-local space
        # 3. Subtract the bone's rest-pose offset in parent-local space
        # This properly handles cases where the armature has rotation and
        # the bone has a parent with a different orientation.

        # Get the bone's rest pose head position in parent-local space
        rest_head_parent_local = mathutils.Vector((0, 0, 0))
        if hip_bone.parent:
            parent_mat_inv = hip_bone.parent.bone.matrix_local.inverted()
            rest_head_parent_local = (parent_mat_inv @ hip_bone.bone.matrix_local).translation

        # Get armature's current object transform (rotation, scale)
        arm_rot_euler = mathutils.Euler((0, 0, 0), 'XYZ')
        if armature_source.rotation_mode == 'QUATERNION':
            arm_rot_euler = armature_source.rotation_quaternion.to_euler('XYZ')
        elif armature_source.rotation_mode == 'AXIS_ANGLE':
            q = mathutils.Quaternion(
                armature_source.rotation_axis_angle[1:],
                armature_source.rotation_axis_angle[0])
            arm_rot_euler = q.to_euler('XYZ')
        else:
            arm_rot_euler = armature_source.rotation_euler.copy()

        arm_scale = armature_source.scale.copy()

        for frame in all_frames:
            bpy.context.scene.frame_set(frame)

            # Read the armature object's animated location at this frame
            arm_location = mathutils.Vector((0, 0, 0))
            for axis, keyframes in location_data.items():
                # Evaluate the fcurve at this frame for smooth interpolation
                for fcurve in obj_location_fcurves:
                    if fcurve.array_index == axis:
                        arm_location[axis] = fcurve.evaluate(frame)
                        break

            # The armature object's world location = arm_location + base_location
            # But for the bone in parent-local space, we need:
            # bone_head_world = armature_matrix_world @ bone_head_local_in_armature
            # armature_matrix_world includes: arm_location, arm_rotation, arm_scale

            # Compute what the armature's world transform is at this frame
            arm_matrix_world = mathutils.Matrix.LocRotScale(arm_location, arm_rot_euler, arm_scale)

            # Compute the bone's world head position WITH the armature location offset
            bone_head_in_armature = hip_bone.bone.matrix_local.translation
            bone_head_world_with_offset = arm_matrix_world @ bone_head_in_armature

            # Convert to parent-local space
            if hip_bone.parent:
                # The parent bone's world matrix (without the armature location offset,
                # since we only want the location delta from armature movement)
                parent_head_world_no_offset = armature_source.matrix_world @ hip_bone.parent.bone.matrix_local.translation
                # Actually we need the full parent bone world transform
                # But since the parent may also be animated, let's use the current depsgraph
                # For simplicity and correctness, compute the offset in armature-local space

                # In armature-local space, the offset from armature location is simply arm_location
                # rotated by the armature's rotation and scaled
                # Then convert to parent-local space
                parent_mat_inv = hip_bone.parent.bone.matrix_local.inverted()
                # The delta in armature-local space is: arm_rot_mat @ arm_loc
                # (armature rotation affects how the location maps to armature space)
                arm_rot_mat = arm_rot_euler.to_matrix().to_4x4()
                arm_loc_in_arm_space = arm_rot_mat @ mathutils.Vector(arm_location) * arm_scale.x

                # Convert this armature-space delta to parent-local space
                location_offset_parent_local = parent_mat_inv @ arm_loc_in_arm_space
                new_bone_location = rest_head_parent_local + location_offset_parent_local.translation
            else:
                # No parent: bone location is in armature-local space directly
                arm_rot_mat = arm_rot_euler.to_matrix().to_4x4()
                arm_loc_in_arm_space = arm_rot_mat @ mathutils.Vector(arm_location) * arm_scale.x
                new_bone_location = rest_head_parent_local + arm_loc_in_arm_space

            hip_bone.location = new_bone_location
            hip_bone.keyframe_insert(data_path='location', frame=frame)

        bpy.ops.object.mode_set(mode='OBJECT')
        print(f'RSL: Armature location transferred to "{hip_bone_name}" successfully '
              f'({len(all_frames)} keyframes, with space conversion)')

    def find_hip_bone_by_name(self, armature):
        """Find a hip-like bone by checking its name against common patterns."""
        if not armature:
            return ''
        for bone in armature.pose.bones:
            bone_name_lower = bone.name.lower().replace('_', '').replace(' ', '')
            for pattern in HIP_BONE_PATTERNS:
                pattern_clean = pattern.replace('_', '').replace(' ', '')
                if pattern_clean in bone_name_lower:
                    return bone.name
        return ''

    def apply_root_motion_offset(self, armature_target, root_motion_rest_offsets):
        """
        After baking, adjust the location keyframes of root motion bones
        so that the target character retains its own rest pose offset.
        """
        if not armature_target.animation_data or not armature_target.animation_data.action:
            return

        action = armature_target.animation_data.action

        for rm_target, offset_data in root_motion_rest_offsets.items():
            rest_offset = offset_data['offset']

            for fcurve in action.fcurves:
                if 'location' not in fcurve.data_path:
                    continue

                bone_name_parts = fcurve.data_path.split('"')
                if len(bone_name_parts) != 3:
                    continue
                if bone_name_parts[1] != rm_target:
                    continue

                axis = fcurve.array_index
                if axis < 3:
                    for kp in fcurve.keyframe_points:
                        kp.co.y += rest_offset[axis]

    def verify_root_motion_location(self, armature_target, root_motion_bones):
        """
        Verify that the baked animation has location keyframes for root motion bones.
        Returns True if location keyframes were found, False otherwise.
        """
        if not armature_target.animation_data or not armature_target.animation_data.action:
            print('RSL: No animation data on target armature - cannot verify root motion')
            return False

        action = armature_target.animation_data.action
        all_ok = True

        for rm_target in root_motion_bones:
            # Look for location fcurves for this bone
            bone_path = f'pose.bones["{rm_target}"].location'
            found_fcurves = []
            for fcurve in action.fcurves:
                if fcurve.data_path == bone_path:
                    found_fcurves.append(fcurve)

            if len(found_fcurves) >= 3:
                # Check if there's actual movement (not all zeros)
                has_movement = False
                for fc in found_fcurves:
                    values = [kp.co.y for kp in fc.keyframe_points]
                    if len(values) > 1 and abs(max(values) - min(values)) > 0.001:
                        has_movement = True
                        break

                if has_movement:
                    print(f'RSL Root Motion: Location keyframes VERIFIED for "{rm_target}" '
                          f'({len(found_fcurves[0].keyframe_points)} keyframes, movement detected)')
                else:
                    print(f'RSL Root Motion WARNING: Location keyframes exist for "{rm_target}" '
                          f'but all values are near-zero (no movement)')
                    all_ok = False
            else:
                print(f'RSL Root Motion WARNING: No location keyframes found for "{rm_target}" '
                      f'(found {len(found_fcurves)} fcurves, need 3)')
                all_ok = False

        return all_ok

    def process_root_motion_after_bake(self, armature_target, root_motion_bones,
                                        root_bones, keep_offset, root_motion_rest_offsets):
        """
        Post-bake processing for root motion.

        UE5 extracts root motion from the PARENTLESS root bone in the skeleton.
        After retargeting, the walking motion is typically on the HIPS bone
        (which has COPY_LOCATION), while the parentless ROOT bone has little
        or no location animation.

        This method ALWAYS extracts motion from HIPS to ROOT. The math is
        always correct regardless of whether ROOT already has some motion:
          delta = HIPS_current_world - HIPS_rest_world
          ROOT.location = delta  (ROOT is parentless, so location = armature space)
          HIPS.location = 0      (motion is now carried by ROOT)

        The resulting HIPS world position is mathematically identical before
        and after the extraction, so the visual pose is preserved.
        """
        if not armature_target.animation_data or not armature_target.animation_data.action:
            print('RSL Root Motion Process: No animation data')
            return

        action = armature_target.animation_data.action

        # Find the ACTUAL parentless ROOT bone in the entire target armature.
        # IMPORTANT: We search ALL pose bones, not just root_bones from the retarget list.
        # The parentless root bone (e.g. "Root" in a UE5 mannequin) is often NOT in the
        # retarget list because there's no matching source bone. The previous code only
        # searched root_bones (which only contains bones from the retarget list), so it
        # would miss "Root" if it wasn't retargeted, and the extraction would silently
        # skip, leaving ROOT with no location animation → UE5 can't extract root motion.
        root_bone_name = ''
        for bone in armature_target.pose.bones:
            if not bone.parent:
                root_bone_name = bone.name
                break

        if not root_bone_name:
            print('RSL Root Motion Process: No parentless root bone found in armature')
            return

        # Get the HIPS bone name from root_motion_bones
        hips_bone_name = ''
        for rm_target in root_motion_bones:
            hips_bone_name = rm_target
            break

        if not hips_bone_name:
            print('RSL Root Motion Process: No hips bone found')
            return

        if root_bone_name == hips_bone_name:
            # Root and Hips are the same bone (e.g. in BVH imports where the
            # parentless bone IS the hips). Root motion is already on the right bone.
            print(f'RSL Root Motion: Root "{root_bone_name}" is the same as HIPS - '
                  f'no extraction needed, motion is already on the parentless bone')
            return

        print(f'RSL Root Motion Process: ROOT="{root_bone_name}", HIPS="{hips_bone_name}"')

        # ALWAYS extract from HIPS to ROOT.
        # This handles all cases correctly:
        # - ROOT has no motion, HIPS has motion → extraction transfers motion to ROOT
        # - ROOT has some motion, HIPS has motion → extraction gives correct combined motion
        # - ROOT has motion, HIPS has none → delta ≈ ROOT's existing motion (no change)
        self.extract_root_motion_to_root_bone(armature_target, hips_bone_name, root_bone_name)

        # Apply rest pose offset
        if keep_offset and root_motion_rest_offsets:
            self.apply_root_motion_offset(armature_target, root_motion_rest_offsets)

    def zero_bone_location(self, action, armature, bone_name):
        """Remove all location keyframes for a bone (set to zero/remove)."""
        fcurves_to_remove = []
        for fcurve in action.fcurves:
            if fcurve.data_path == f'pose.bones["{bone_name}"].location':
                fcurves_to_remove.append(fcurve)

        for fcurve in fcurves_to_remove:
            action.fcurves.remove(fcurve)

        # Also clear the pose bone's current location
        bone = armature.pose.bones.get(bone_name)
        if bone:
            bone.location = (0, 0, 0)

        if fcurves_to_remove:
            print(f'RSL: Removed {len(fcurves_to_remove)} location fcurves from "{bone_name}"')

    def extract_root_motion_to_root_bone(self, armature_target, hips_bone_name, root_bone_name):
        """
        Extract root motion from the HIPS bone to the parentless ROOT bone.

        UE5 extracts root motion from the parentless ROOT bone (the one with no
        parent). After retargeting, the HIPS bone has location animation (forward
        walking, etc.) but the ROOT bone has none. This means UE5 sees no root
        motion.

        This method:
        1. Reads the HIPS bone's pose position at EVERY frame of the animation
        2. Computes the delta from the rest pose (in armature space)
        3. Sets the ROOT bone's location to this delta (ROOT has no parent,
           so ROOT.location is in armature space)
        4. Zeros out the HIPS bone's location (the motion is now on ROOT)

        The math is always correct regardless of whether ROOT already had some
        location animation, because:
          delta = HIPS_current_world - HIPS_rest_world
          ROOT.location = delta   (carries all displacement)
          HIPS.location = 0       (subtracted from HIPS, compensated by ROOT)
          → HIPS world position is preserved exactly

        Parameters:
          armature_target: The target armature with baked animation
          hips_bone_name: Name of the HIPS bone (has walking location animation)
          root_bone_name: Name of the parentless ROOT bone (needs to receive motion)
        """
        if not armature_target.animation_data or not armature_target.animation_data.action:
            print('RSL Root Motion Extract: No animation data on target')
            return

        action = armature_target.animation_data.action

        root_bone = armature_target.pose.bones.get(root_bone_name)
        hips_bone = armature_target.pose.bones.get(hips_bone_name)

        if not root_bone:
            print(f'RSL Root Motion Extract: ROOT bone "{root_bone_name}" not found')
            return
        if not hips_bone:
            print(f'RSL Root Motion Extract: HIPS bone "{hips_bone_name}" not found')
            return

        # Get the full frame range of the animation
        frame_start = None
        frame_end = None
        for fcurve in action.fcurves:
            for kp in fcurve.keyframe_points:
                f = kp.co.x
                if frame_start is None or f < frame_start:
                    frame_start = f
                if frame_end is None or f > frame_end:
                    frame_end = f

        if frame_start is None or frame_end is None:
            print('RSL Root Motion Extract: No keyframes found in action')
            return

        frame_start = int(frame_start)
        frame_end = int(frame_end)

        print(f'RSL Root Motion Extract: Extracting from "{hips_bone_name}" to '
              f'"{root_bone_name}" (frames {frame_start}-{frame_end})')

        # Switch to object mode for depsgraph evaluation
        bpy.context.view_layer.objects.active = armature_target
        if armature_target.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # First pass: read HIPS head position at EVERY frame of the animation.
        # We must do this BEFORE modifying any bone locations, because changing
        # ROOT.location would shift HIPS' computed position (HIPS is a child of ROOT).
        hips_head_per_frame = {}
        for frame in range(frame_start, frame_end + 1):
            bpy.context.scene.frame_set(frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            armature_eval = armature_target.evaluated_get(depsgraph)
            hips_bone_eval = armature_eval.pose.bones.get(hips_bone_name)
            if hips_bone_eval:
                hips_head_per_frame[frame] = hips_bone_eval.head.copy()
            else:
                hips_head_per_frame[frame] = hips_bone.head.copy()

        # Also read at frame_start-1 if possible to get the "rest" position
        # Use the first frame as the reference (rest pose position)
        rest_frame = frame_start
        hips_rest_head = hips_head_per_frame[rest_frame].copy()
        root_rest_head = root_bone.bone.head_local.copy()

        print(f'  HIPS rest head (frame {rest_frame}): {hips_rest_head}')
        print(f'  ROOT rest head (bone.head_local): {root_rest_head}')
        if frame_end > frame_start:
            print(f'  HIPS head at frame {frame_end}: {hips_head_per_frame[frame_end]}')
            delta_end = hips_head_per_frame[frame_end] - hips_rest_head
            print(f'  Delta at frame {frame_end}: {delta_end}')
            delta_len = delta_end.length
            print(f'  Delta length at last frame: {delta_len:.4f}')
            if delta_len < 0.01:
                print(f'  WARNING: Delta is very small! HIPS bone may not have walking motion.')
                print(f'  This usually means the source animation has no root/hip translation,')
                print(f'  or the COPY_LOCATION constraint did not produce location keyframes.')

        # Switch to pose mode for keyframe insertion
        bpy.ops.object.mode_set(mode='POSE')

        # Second pass: set ROOT and HIPS locations at every frame
        for frame in range(frame_start, frame_end + 1):
            bpy.context.scene.frame_set(frame)

            # The delta between current HIPS position and HIPS rest position
            # is the root motion displacement, in armature space.
            # Since ROOT has no parent, ROOT.location IS armature space,
            # so this delta is exactly what ROOT.location should be.
            delta = hips_head_per_frame[frame] - hips_rest_head

            # Set ROOT's location to carry the root motion
            root_bone.location = delta
            root_bone.keyframe_insert(data_path='location', frame=frame)

            # Zero out HIPS' location. The motion is now on ROOT.
            # This preserves the visual pose because:
            #   new_hips_world = ROOT_delta + ROOT_rot * (HIPS_rest + 0)
            #                 = ROOT_delta + ROOT_rot * HIPS_rest
            # And the original was:
            #   old_hips_world = old_ROOT_loc + ROOT_rot * (HIPS_rest + old_HIPS_loc)
            # Since ROOT_delta = old_hips_world - ROOT_rot * HIPS_rest,
            # the two are equal.
            hips_bone.location = (0, 0, 0)
            hips_bone.keyframe_insert(data_path='location', frame=frame)

        bpy.ops.object.mode_set(mode='OBJECT')

        # Clean up: remove HIPS location fcurves that are all zeros
        fcurves_to_check = []
        for fcurve in action.fcurves:
            if fcurve.data_path == f'pose.bones["{hips_bone_name}"].location':
                fcurves_to_check.append(fcurve)

        for fcurve in fcurves_to_check:
            all_zero = all(abs(kp.co.y) < 0.0001 for kp in fcurve.keyframe_points)
            if all_zero:
                action.fcurves.remove(fcurve)

        # Verify ROOT location was set correctly
        root_loc_fcurves = []
        for fcurve in action.fcurves:
            if fcurve.data_path == f'pose.bones["{root_bone_name}"].location':
                root_loc_fcurves.append(fcurve)

        if len(root_loc_fcurves) >= 3:
            has_movement = False
            max_delta = 0.0
            for fc in root_loc_fcurves:
                values = [kp.co.y for kp in fc.keyframe_points]
                if len(values) > 1:
                    spread = abs(max(values) - min(values))
                    max_delta = max(max_delta, spread)
                    if spread > 0.001:
                        has_movement = True

            if has_movement:
                print(f'RSL Root Motion Extract: SUCCESS - ROOT "{root_bone_name}" now has '
                      f'location animation ({len(root_loc_fcurves[0].keyframe_points)} keyframes, '
                      f'max delta={max_delta:.4f})')
            else:
                print(f'RSL Root Motion Extract: WARNING - ROOT "{root_bone_name}" has location '
                      f'keyframes but no significant movement (max delta={max_delta:.6f})')
                print(f'  This usually means the HIPS bone had no walking motion either.')
                print(f'  Check that the source animation actually has root/hip translation.')
        else:
            print(f'RSL Root Motion Extract: WARNING - ROOT "{root_bone_name}" has only '
                  f'{len(root_loc_fcurves)} location fcurves (expected 3)')

    def transfer_root_motion_direct(self, armature_source, armature_target,
                                     root_motion_bones, root_bones_with_motion):
        """
        Fallback: Directly transfer root motion location from source to target action.
        This is used when the constraint-based COPY_LOCATION approach fails to produce
        location keyframes during baking.

        Strategy: Read the source bone's world-space position at each frame,
        convert it to the target bone's parent space, and set as location keyframes.
        """
        if not armature_source.animation_data or not armature_source.animation_data.action:
            return
        if not armature_target.animation_data or not armature_target.animation_data.action:
            return

        source_action = armature_source.animation_data.action
        target_action = armature_target.animation_data.action

        for rm_target, rm_source in root_motion_bones.items():
            source_bone = armature_source.pose.bones.get(rm_source)
            target_bone = armature_target.pose.bones.get(rm_target)

            if not source_bone or not target_bone:
                print(f'RSL Direct Transfer: Bone not found - source="{rm_source}", target="{rm_target}"')
                continue

            # Find all frames in the source animation
            frame_set = set()
            for fcurve in source_action.fcurves:
                for kp in fcurve.keyframe_points:
                    frame_set.add(int(round(kp.co.x)))

            if not frame_set:
                print(f'RSL Direct Transfer: No frames found in source action')
                continue

            frames = sorted(frame_set)
            print(f'RSL Direct Transfer: Transferring {len(frames)} frames for "{rm_target}"')

            # Get the target bone's parent inverse matrix for space conversion
            target_parent_inv = mathutils.Matrix.Identity(4)
            if target_bone.parent:
                target_parent_inv = target_bone.parent.bone.matrix_local.inverted()

            # Get the target armature's world matrix inverse
            target_armature_inv = armature_target.matrix_world.inverted()

            # Read source bone's rest location for offset calculation
            source_rest_loc = source_bone.bone.matrix_local.translation.copy()
            if source_bone.parent:
                source_rest_loc = (source_bone.parent.bone.matrix_local.inverted() @ source_bone.bone.matrix_local).translation

            # Switch to pose mode for keyframe insertion
            bpy.context.view_layer.objects.active = armature_target
            if armature_target.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.object.mode_set(mode='POSE')

            # Sample the source animation at each frame and set target bone location
            for frame in frames:
                bpy.context.scene.frame_set(frame)

                # Get source bone's current pose location in its parent space
                source_loc = source_bone.location.copy()

                # Get source bone's world position
                source_head_ws = armature_source.matrix_world @ source_bone.head.copy()

                # Convert to target bone's parent local space
                target_head_local = target_armature_inv @ source_head_ws
                if target_bone.parent:
                    target_head_local = target_bone.parent.bone.matrix_local.inverted() @ target_head_local

                # Subtract the target bone's rest pose head position to get the offset
                target_rest_head = target_bone.bone.matrix_local.translation.copy()
                if target_bone.parent:
                    target_rest_head = (target_bone.parent.bone.matrix_local.inverted() @ target_bone.bone.matrix_local).translation

                location_offset = target_head_local - target_rest_head

                # Set the target bone's location
                target_bone.location = location_offset
                target_bone.keyframe_insert(data_path='location', frame=frame)

            bpy.ops.object.mode_set(mode='OBJECT')
            print(f'RSL Direct Transfer: Successfully transferred root motion for "{rm_target}" ({len(frames)} frames)')

    def clean_animation(self, armature_source):
        """Remove armature-object-level animation fcurves that interfere with auto-scaling.

        All object-level fcurves are removed including location, because:
        - Location data has already been transferred to the hip bone by
          transfer_armature_location_to_hip() if it contained root motion
        - Remaining object-level animation interferes with the retargeting process
        """
        deletable_fcurves = ['rotation_euler', 'rotation_quaternion', 'scale', 'location']
        fcurves_to_remove = []
        for fcurve in armature_source.animation_data.action.fcurves:
            if fcurve.data_path in deletable_fcurves:
                fcurves_to_remove.append(fcurve)
        for fcurve in fcurves_to_remove:
            armature_source.animation_data.action.fcurves.remove(fcurve)

    def get_and_reset_pose_rotations(self, armature):
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature)
        bpy.ops.object.mode_set(mode='POSE')

        pose_rotations = {}
        for bone in armature.pose.bones:
            if bone.rotation_mode == 'QUATERNION':
                pose_rotations[bone.name] = copy.deepcopy(bone.rotation_quaternion)
                bone.rotation_quaternion = (1, 0, 0, 0)
            else:
                pose_rotations[bone.name] = copy.deepcopy(bone.rotation_euler)
                bone.rotation_euler = (0, 0, 0)

        bpy.ops.object.mode_set(mode='OBJECT')

        return pose_rotations

    def load_pose_rotations(self, armature, pose_rotations):
        if not pose_rotations:
            return

        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature)
        bpy.ops.object.mode_set(mode='POSE')

        for bone in armature.pose.bones:
            rot = pose_rotations.get(bone.name)
            if rot:
                if bone.rotation_mode == 'QUATERNION':
                    bone.rotation_quaternion = rot
                else:
                    bone.rotation_euler = rot

        bpy.ops.object.mode_set(mode='OBJECT')

    def scale_armature(self, context, armature_source, armature_target, root_bones):
        source_min = None
        source_min_root = None
        target_min = None
        target_min_root = None

        for item in self.retarget_bone_list:
            bone_source = armature_source.pose.bones.get(item.bone_name_source)
            bone_target = armature_target.pose.bones.get(item.bone_name_target)

            bone_source_z = (armature_source.matrix_world @ bone_source.head)[2]
            bone_target_z = (armature_target.matrix_world @ bone_target.head)[2]

            if item.bone_name_target in root_bones:
                if source_min_root is None or source_min_root > bone_source_z:
                    source_min_root = bone_source_z
                if target_min_root is None or target_min_root > bone_target_z:
                    target_min_root = bone_target_z

            if source_min is None or source_min > bone_source_z:
                source_min = bone_source_z
            if target_min is None or target_min > bone_target_z:
                target_min = bone_target_z

        source_height = source_min_root - source_min
        target_height = target_min_root - target_min

        if not source_height or not target_height:
            print('No scaling needed')
            return

        scale_factor = target_height / source_height
        armature_source.scale *= scale_factor

    def read_anim_start_end(self, armature):
        frame_start = None
        frame_end = None
        for fcurve in armature.animation_data.action.fcurves:
            for key in fcurve.keyframe_points:
                keyframe = key.co.x
                if frame_start is None:
                    frame_start = keyframe
                if frame_end is None:
                    frame_end = keyframe

                if keyframe < frame_start:
                    frame_start = keyframe
                if keyframe > frame_end:
                    frame_end = keyframe

        return frame_start, frame_end

    def copy_rest_pose(self, context, armature_source):
        context.scene.tool_settings.use_keyframe_insert_auto = False

        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature_source)
        bpy.ops.object.mode_set(mode='OBJECT')

        bpy.ops.object.duplicate_move(OBJECT_OT_duplicate={"linked": False, "mode": 'TRANSLATION'},
                                      TRANSFORM_OT_translate={"value": (0, 0, 0), "constraint_axis": (False, True, False), "mirror": False, "snap": False, "remove_on_cancel": False,
                                                              "release_confirm": False})

        source_armature_copy = context.object
        source_armature_copy.name = armature_source.name + "_copy"

        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(source_armature_copy)
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='POSE')

        action_tmp = source_armature_copy.animation_data.action
        source_armature_copy.animation_data.action = None
        bpy.ops.pose.armature_apply()
        source_armature_copy.animation_data.action = action_tmp

        for bone in source_armature_copy.pose.bones:
            constraint = bone.constraints.new('COPY_TRANSFORMS')
            constraint.name = bone.name
            constraint.target = armature_source
            constraint.subtarget = bone.name

        bpy.ops.object.mode_set(mode='OBJECT')

        return source_armature_copy

    def bake_animation(self, armature_source, armature_target, root_bones):
        """Bake the visual pose of the target armature to keyframes.

        root_bones: list of bone names whose LOCATION keyframes should be preserved.
        This includes both parentless root bones AND root motion bones (like hips).
        """
        frame_split = 25
        frame_start, frame_end = self.read_anim_start_end(armature_source)
        frame_start, frame_end = int(frame_start), int(frame_end)
        utils.set_active(armature_target)

        print(f'RSL bake_animation: root_bones (location preserved) = {root_bones}')

        actions_all = []

        current_step = 0
        steps = int((frame_end - frame_start) / frame_split) + 1
        wm = bpy.context.window_manager
        wm.progress_begin(current_step, steps)

        import time
        start_time = time.time()

        bpy.ops.object.mode_set(mode='POSE')
        for frame in range(frame_start, frame_end + 2, frame_split):
            start = frame
            end = frame + frame_split - 1
            if end > frame_end:
                end = frame_end
            if start > end:
                continue

            bpy.ops.nla.bake(frame_start=start, frame_end=end, visual_keying=True, only_selected=True, use_current_action=False, bake_types={'POSE'})

            armature_target.animation_data.action.name = 'RSL_RETARGETING_' + str(frame)

            actions_all.append(armature_target.animation_data.action)

            current_step += 1
            if steps != current_step:
                wm.progress_update(current_step)
        bpy.ops.object.mode_set(mode='OBJECT')

        if not actions_all:
            return

        key_counts = {}
        for action in actions_all:
            for fcurve in action.fcurves:
                key = fcurve.data_path + str(fcurve.array_index)
                if not key_counts.get(key):
                    key_counts[key] = 0
                key_counts[key] += len(fcurve.keyframe_points)

        action_final = bpy.data.actions.new(name='RSL_RETARGETING_FINAL')
        action_final.use_fake_user = False
        armature_target.animation_data_create().action = action_final

        print_i = 0
        for fcurve in actions_all[0].fcurves:
            if fcurve.data_path.endswith('scale'):
                continue
            if fcurve.data_path.endswith('location'):
                bone_name = fcurve.data_path.split('"')
                if len(bone_name) != 3:
                    continue
                # Keep location keyframes for root bones AND root motion bones
                if bone_name[1] not in root_bones:
                    continue
                else:
                    print(f'RSL bake: Preserving location for bone "{bone_name[1]}" (in root_bones list)')

            curve_final = action_final.fcurves.new(data_path=fcurve.data_path, index=fcurve.array_index, action_group=fcurve.group.name)
            keyframe_points = curve_final.keyframe_points
            keyframe_points.add(key_counts[fcurve.data_path + str(fcurve.array_index)])

            index = 0
            for action in actions_all:
                fcruve_to_add = action.fcurves.find(data_path=fcurve.data_path, index=fcurve.array_index)

                for kp in fcruve_to_add.keyframe_points:
                    keyframe_points[index].co.x = kp.co.x
                    keyframe_points[index].co.y = kp.co.y
                    keyframe_points[index].interpolation = 'LINEAR'
                    index += 1

            print_i += 1

        for fcurve in action_final.fcurves:
            if len(fcurve.keyframe_points) <= 2:
                continue

            kp_pre_pre = fcurve.keyframe_points[0]
            kp_pre = fcurve.keyframe_points[1]

            kp_to_delete = []
            for kp in fcurve.keyframe_points[2:]:
                if round(kp_pre_pre.co.y, 5) == round(kp_pre.co.y, 5) == round(kp.co.y, 5):
                    kp_to_delete.append(kp_pre)
                kp_pre_pre = kp_pre
                kp_pre = kp

            for kp in reversed(kp_to_delete):
                fcurve.keyframe_points.remove(kp)

        for action in actions_all:
            bpy.data.actions.remove(action)

        # Clean up NLA tracks left over from the bake process.
        # Each nla.bake(use_current_action=False) pushes the previous action to
        # an NLA strip. These stale strips cause FBX export to include multiple
        # animations (e.g. 7 instead of 1 in UE5).
        if armature_target.animation_data:
            for track in list(armature_target.animation_data.nla_tracks):
                armature_target.animation_data.nla_tracks.remove(track)
            print(f'RSL: Cleaned up NLA tracks after bake')

        print('Retargeting Time:', round(time.time() - start_time, 2), 'seconds')
        wm.progress_end()

        if hasattr(armature_target.animation_data, "action_slot"):
            armature_target.animation_data.action_slot = armature_target.animation_data.action_suitable_slots[0]
