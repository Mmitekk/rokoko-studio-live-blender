import bpy
import copy
import math

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
]


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

        This does three things:
        1. Reads the current rotation of each bone from the T-pose reference armature
        2. Removes any existing rotation keyframes at the target frame from the source animation
        3. Sets the source bone rotations to match the T-pose reference and inserts keyframes

        The bone matching is done by name — bones with the same name in both armatures
        will be matched automatically.

        Args:
            tpose_ref: The armature that is in T-pose (reference)
            armature_source: The armature with the animation (target for T-pose application)
            frame: The frame number to apply T-pose on (default: 1)

        Returns:
            The number of bones that had their rotations copied
        """
        # Go to the target frame
        bpy.context.scene.frame_set(frame)

        # 1. Read rotations from the T-pose reference armature
        src_rots = {}
        for b in tpose_ref.pose.bones:
            # Store the rotation in whatever mode the bone uses
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
            fcurves_to_clean = []
            for fc in act.fcurves:
                if "rotation_euler" in fc.data_path or "rotation_quaternion" in fc.data_path:
                    pts = fc.keyframe_points
                    # Remove from end to not break indices
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

        # --- Root Motion Logic ---
        # Determine which bones carry root motion based on user settings
        root_motion_mode = context.scene.rsl_retargeting_root_motion_mode
        root_motion_bones = {}  # dict: target_bone_name -> source_bone_name

        if root_motion_mode != 'OFF':
            if root_motion_mode == 'AUTO':
                # Auto-detect hip/root motion bone from bone list
                rm_source, rm_target = self.find_root_motion_bones_auto(
                    armature_source, armature_target, root_bones)
            else:  # CUSTOM
                rm_source = context.scene.rsl_retargeting_root_bone_source
                rm_target = context.scene.rsl_retargeting_root_bone_target

                # Validate custom root motion bones
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

        # Merge root_motion_bones into root_bones for location baking purposes
        # root_bones_with_motion includes both parentless root bones AND the root motion bone
        root_bones_with_motion = list(root_bones)
        for rm_target in root_motion_bones:
            if rm_target not in root_bones_with_motion:
                root_bones_with_motion.append(rm_target)

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
        # This fixes the common issue where BVH animations from Kimodo or other sources
        # have a rest pose (A-pose) that differs from T-pose, causing arms to freeze
        tpose_applied = False
        tpose_ref = context.scene.rsl_retargeting_tpose_reference
        if tpose_ref and context.scene.rsl_retargeting_tpose_apply_before:
            if (tpose_ref.type == 'ARMATURE' and
                    armature_source.animation_data and armature_source.animation_data.action):
                count = ApplyTPoseReference.apply_tpose(tpose_ref, armature_source, frame=1)
                tpose_applied = True
                print(f'RSL: T-Pose reference applied before retargeting ({count} bones)')

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
            # Clean source animation
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
                    # Save the rest-pose head positions in world space
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
                                         utils.mat3_to_vec_roll(armature_source.matrix_world.inverted().to_3x3() @ bone.matrix.to_3x3())  # Head loc, tail loc, bone roll

        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature_source)
        bpy.ops.object.mode_set(mode='EDIT')

        # Recreate bones from target armature in source armature
        for item in self.retarget_bone_list:
            bone_source = armature_source.data.edit_bones.get(item.bone_name_source)

            # Recreate target bone
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

            # Add COPY_LOCATION for root motion bones (e.g., hip bone)
            # This is the key fix: hip bones that have parents also get location transfer
            elif bone_target.name in root_motion_bones:
                rm_source_name = root_motion_bones[bone_target.name]
                constraint = bone_target.constraints.new('COPY_LOCATION')
                constraint.name += RETARGET_ID
                constraint.target = armature_source
                constraint.subtarget = rm_source_name

            # Select the bone for animation
            armature_target.data.bones.get(item.bone_name_target).select = True

        # Bake the animation to the target armature
        # Use root_bones_with_motion so location data is kept for root motion bones too
        self.bake_animation(armature_source, armature_target, root_bones_with_motion)

        # --- Root Motion: Apply rest pose offset to baked location keyframes ---
        if root_motion_bones and context.scene.rsl_retargeting_root_motion_keep_offset:
            self.apply_root_motion_offset(armature_target, root_motion_rest_offsets)

        # Delete the duplicate helper armature
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature_source)
        bpy.data.actions.remove(armature_source.animation_data.action)
        bpy.ops.object.delete()

        # Change armature source back to original
        armature_source = armature_source_original

        # Change action name
        armature_target.animation_data.action.name = armature_source.animation_data.action.name + ' Retarget'

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

        Strategy:
        1. Look for bones in the retarget list whose key is 'hip' or similar
        2. Fallback: search by bone name patterns in both armatures
        3. If still not found, use the first animated bone that has location data in the source action
        """
        # Strategy 1: Check the bone list for hip-like bones by their detection key
        for item in self.retarget_bone_list:
            if not item.bone_name_target:
                continue
            bone_key_lower = item.bone_name_key.lower()
            if bone_key_lower in ('hip', 'hips', 'pelvis'):
                return item.bone_name_source, item.bone_name_target

        # Strategy 2: Check source bone names against common hip patterns
        for item in self.retarget_bone_list:
            if not item.bone_name_target:
                continue
            source_lower = item.bone_name_source.lower().replace('_', '').replace(' ', '')
            for pattern in HIP_BONE_PATTERNS:
                pattern_clean = pattern.replace('_', '').replace(' ', '')
                if pattern_clean in source_lower:
                    return item.bone_name_source, item.bone_name_target

        # Strategy 3: Look for animated location data on bones in the source action
        # The hip bone typically has the most location keyframes
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

        # Find the bone with the most location keyframes that's also in our retarget list
        if bone_location_counts:
            # Sort by number of location keyframes (descending)
            sorted_bones = sorted(bone_location_counts.items(), key=lambda x: x[1], reverse=True)
            for bone_name, _ in sorted_bones:
                # Check if this bone is in the retarget list
                for item in self.retarget_bone_list:
                    if item.bone_name_source == bone_name and item.bone_name_target:
                        # Also verify it's not already a parentless root bone
                        if bone_name not in [rb for rb in root_bones]:
                            print(f'RSL Root Motion Auto: Found bone with location data: {bone_name}')
                            return item.bone_name_source, item.bone_name_target

        return '', ''

    def apply_root_motion_offset(self, armature_target, root_motion_rest_offsets):
        """
        After baking, adjust the location keyframes of root motion bones
        so that the target character retains its own rest pose offset.

        Without this, the hip bone location from the source armature is copied
        directly, which ignores the target armature's rest pose hip position.
        With offset, we add the difference between target and source rest positions.
        """
        if not armature_target.animation_data or not armature_target.animation_data.action:
            return

        action = armature_target.animation_data.action

        for rm_target, offset_data in root_motion_rest_offsets.items():
            rest_offset = offset_data['offset']

            # Find and modify location fcurves for the root motion bone
            for fcurve in action.fcurves:
                if 'location' not in fcurve.data_path:
                    continue

                # Extract bone name from data_path like: pose.bones["Hips"].location
                bone_name_parts = fcurve.data_path.split('"')
                if len(bone_name_parts) != 3:
                    continue
                if bone_name_parts[1] != rm_target:
                    continue

                # Apply offset to the corresponding axis
                axis = fcurve.array_index
                if axis < 3:
                    for kp in fcurve.keyframe_points:
                        kp.co.y += rest_offset[axis]

    def clean_animation(self, armature_source):
        deletable_fcurves = ['location', 'rotation_euler', 'rotation_quaternion', 'scale']
        for fcurve in armature_source.animation_data.action.fcurves:
            if fcurve.data_path in deletable_fcurves:
                armature_source.animation_data.action.fcurves.remove(fcurve)

    def get_and_reset_pose_rotations(self, armature):
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature)
        bpy.ops.object.mode_set(mode='POSE')

        # Save rotations
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

        # Load rotations
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
        # make sure auto keyframe is disabled, leads to issues
        context.scene.tool_settings.use_keyframe_insert_auto = False

        # ensure the source armature selection
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature_source)
        bpy.ops.object.mode_set(mode='OBJECT')

        # Duplicate the source armature
        bpy.ops.object.duplicate_move(OBJECT_OT_duplicate={"linked": False, "mode": 'TRANSLATION'},
                                      TRANSFORM_OT_translate={"value": (0, 0, 0), "constraint_axis": (False, True, False), "mirror": False, "snap": False, "remove_on_cancel": False,
                                                              "release_confirm": False})

        # Set name of the copied source armature
        source_armature_copy = context.object
        source_armature_copy.name = armature_source.name + "_copy"

        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(source_armature_copy)
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='POSE')

        # Apply transforms of the new source armature. Unlink action temporarily to prevent warning in console
        action_tmp = source_armature_copy.animation_data.action
        source_armature_copy.animation_data.action = None
        bpy.ops.pose.armature_apply()
        source_armature_copy.animation_data.action = action_tmp

        # Mimic the animation of the original source armature by adding constraints to the bones.
        # -> the new armature has the exact same animation but with applied transforms
        for bone in source_armature_copy.pose.bones:
            constraint = bone.constraints.new('COPY_TRANSFORMS')
            constraint.name = bone.name
            constraint.target = armature_source
            constraint.subtarget = bone.name

        bpy.ops.object.mode_set(mode='OBJECT')

        return source_armature_copy

    def bake_animation(self, armature_source, armature_target, root_bones):
        frame_split = 25
        frame_start, frame_end = self.read_anim_start_end(armature_source)
        frame_start, frame_end = int(frame_start), int(frame_end)
        utils.set_active(armature_target)

        actions_all = []

        # Setup loading bar
        current_step = 0
        steps = int((frame_end - frame_start) / frame_split) + 1
        wm = bpy.context.window_manager
        wm.progress_begin(current_step, steps)

        import time
        start_time = time.time()

        # Bake the animation in parts because multiple short parts are processed much faster than one long animation
        bpy.ops.object.mode_set(mode='POSE')
        for frame in range(frame_start, frame_end + 2, frame_split):
            start = frame
            end = frame + frame_split - 1
            if end > frame_end:
                end = frame_end
            if start > end:
                continue

            # Bake animation part
            bpy.ops.nla.bake(frame_start=start, frame_end=end, visual_keying=True, only_selected=True, use_current_action=False, bake_types={'POSE'})

            # Rename animation part
            armature_target.animation_data.action.name = 'RSL_RETARGETING_' + str(frame)

            actions_all.append(armature_target.animation_data.action)

            current_step += 1
            if steps != current_step:
                wm.progress_update(current_step)
        bpy.ops.object.mode_set(mode='OBJECT')

        if not actions_all:
            return

        # Count all keys for all data_paths
        key_counts = {}
        for action in actions_all:
            for fcurve in action.fcurves:
                key = fcurve.data_path + str(fcurve.array_index)
                if not key_counts.get(key):
                    key_counts[key] = 0
                key_counts[key] += len(fcurve.keyframe_points)

        # Create new action
        action_final = bpy.data.actions.new(name='RSL_RETARGETING_FINAL')
        action_final.use_fake_user = True
        armature_target.animation_data_create().action = action_final

        # Put all baked animations parts back together into one
        print_i = 0
        for fcurve in actions_all[0].fcurves:
            if fcurve.data_path.endswith('scale'):
                continue
            if fcurve.data_path.endswith('location'):
                bone_name = fcurve.data_path.split('"')
                if len(bone_name) != 3:
                    continue
                # KEY FIX: Allow location keyframes for both parentless root bones
                # AND root motion bones (e.g., hip bone with a parent)
                if bone_name[1] not in root_bones:
                    continue

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

        # Clean up animation. Delete all keyframes that use the same value as the previous and next one
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

        # Delete all baked animation parts, only the combined one is needed
        for action in actions_all:
            bpy.data.actions.remove(action)

        print('Retargeting Time:', round(time.time() - start_time, 2), 'seconds')
        wm.progress_end()

        # Set the action slot sub action
        if hasattr(armature_target.animation_data, "action_slot"):
            armature_target.animation_data.action_slot = armature_target.animation_data.action_suitable_slots[0]
