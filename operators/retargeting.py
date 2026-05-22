import bpy
import copy
import math
import mathutils

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
    
    Fixes common issues:
    - Applies object-level rotation (90° X offset from BVH import)
    - Sets the armature scale to 1.0 (prevents oversized import in UE5)
    - Corrects forward/up orientation for UE5
    
    Usage: Select the retargeted armature, then click this button before exporting as FBX.
    """
    bl_idname = "rsl.prepare_for_ue5"
    bl_label = "Prepare for UE5 Export"
    bl_description = ('Prepares the retargeted armature for Unreal Engine FBX export. '
                      'Applies object rotation and normalizes scale to prevent '
                      'flipped orientation and oversized import in UE5.')
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

        changes_made = []

        # 1. Apply object rotation (fixes 90° X rotation from BVH import)
        if armature.rotation_mode == 'QUATERNION':
            rot = armature.rotation_quaternion
            has_rotation = abs(rot.w - 1.0) > 0.001 or abs(rot.x) > 0.001 or abs(rot.y) > 0.001 or abs(rot.z) > 0.001
        elif armature.rotation_mode == 'AXIS_ANGLE':
            rot = armature.rotation_axis_angle
            has_rotation = abs(rot[0]) > 0.001
        else:
            rot = armature.rotation_euler
            has_rotation = abs(rot.x) > 0.001 or abs(rot.y) > 0.001 or abs(rot.z) > 0.001

        if has_rotation:
            # Apply rotation to the armature's bones
            utils.set_active(armature)
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
            changes_made.append('Rotation applied')
            print(f'RSL UE5 Prep: Applied rotation on "{armature.name}"')
        else:
            print('RSL UE5 Prep: No rotation to apply')

        # 2. Normalize scale to 1.0
        has_non_uniform_scale = (
            abs(armature.scale.x - 1.0) > 0.001 or
            abs(armature.scale.y - 1.0) > 0.001 or
            abs(armature.scale.z - 1.0) > 0.001
        )

        if has_non_uniform_scale:
            utils.set_active(armature)
            bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            changes_made.append('Scale applied')
            print(f'RSL UE5 Prep: Applied scale on "{armature.name}"')
        else:
            print('RSL UE5 Prep: Scale already at 1.0')

        # 3. Set armature to rest position temporarily to verify
        armature.data.pose_position = 'REST'
        armature.data.pose_position = 'POSE'

        if changes_made:
            self.report({'INFO'}, f'UE5 prep done: {", ".join(changes_made)}. '
                                  f'Now export as FBX with Forward=-Z, Up=Y, Scale=1.0')
        else:
            self.report({'INFO'}, 'Armature already prepared for UE5 export. '
                                  'Export as FBX with Forward=-Z, Up=Y, Scale=1.0')

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

        # Bake the animation to the target armature
        self.bake_animation(armature_source, armature_target, root_bones_with_motion)

        # --- Root Motion: Verify location keyframes and apply fallback if needed ---
        if root_motion_bones:
            location_verified = self.verify_root_motion_location(armature_target, root_motion_bones)
            if not location_verified:
                print('RSL WARNING: Root motion location keyframes missing after bake. Applying direct transfer fallback...')
                self.transfer_root_motion_direct(armature_source, armature_target, root_motion_bones, root_bones_with_motion)

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
        KEY FIX: Transfer armature-object-level location keyframes to the hip bone.

        When BVH files are imported into Blender, the root motion (forward walking etc.)
        is often stored as the armature OBJECT's location animation, not as a bone's
        location. The original plugin's clean_animation() method deletes these object-level
        location fcurves, which destroys the root motion data entirely.

        This method detects if the armature object has location keyframes, and if so,
        transfers them to the hip/root motion bone BEFORE any cleanup happens.
        This ensures root motion survives the retargeting process.
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

        # Switch to pose mode on the source armature
        bpy.context.view_layer.objects.active = armature_source
        if armature_source.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='POSE')

        # Read all armature-level location keyframes
        location_data = {}  # axis -> [(frame, value)]
        for fcurve in obj_location_fcurves:
            axis = fcurve.array_index
            if axis not in location_data:
                location_data[axis] = []
            for kp in fcurve.keyframe_points:
                location_data[axis].append((kp.co.x, kp.co.y))

        # Get the bone's current rest location offset
        # We need to ADD the armature location to the bone's current location
        bone_rest_location = copy.deepcopy(hip_bone.location)

        # Set location keyframes on the hip bone
        # For each axis with armature-level data
        for axis, keyframes in location_data.items():
            data_path = 'location'
            for frame, value in keyframes:
                # Set the bone's location at this frame
                # The bone location = rest_location + armature_location_offset
                hip_bone.location[axis] = bone_rest_location[axis] + value
                hip_bone.keyframe_insert(data_path=data_path, index=axis, frame=frame)

        bpy.ops.object.mode_set(mode='OBJECT')
        print(f'RSL: Armature location transferred to "{hip_bone_name}" successfully')

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
        
        NOTE: Only removes rotation and scale at the object level.
        Location fcurves at the object level are NO LONGER removed because they
        often contain root motion data from BVH imports. The location data is
        transferred to the hip bone by transfer_armature_location_to_hip() before
        this method is called.
        """
        # Only remove rotation and scale at armature level - NOT location
        # Location may contain root motion data that was already transferred to hip bone
        deletable_fcurves = ['rotation_euler', 'rotation_quaternion', 'scale']
        for fcurve in armature_source.animation_data.action.fcurves:
            if fcurve.data_path in deletable_fcurves:
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
        frame_split = 25
        frame_start, frame_end = self.read_anim_start_end(armature_source)
        frame_start, frame_end = int(frame_start), int(frame_end)
        utils.set_active(armature_target)

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
        action_final.use_fake_user = True
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

        print('Retargeting Time:', round(time.time() - start_time, 2), 'seconds')
        wm.progress_end()

        if hasattr(armature_target.animation_data, "action_slot"):
            armature_target.animation_data.action_slot = armature_target.animation_data.action_suitable_slots[0]
