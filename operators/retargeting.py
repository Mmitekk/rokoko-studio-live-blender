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
      Forward: -Y Forward | Up: Z Up | Scale: 1.0 | Apply Scalings: FBX All
    Or simply use the "Export FBX for UE5" button which does everything
    automatically.
    """
    bl_idname = "rsl.prepare_for_ue5"
    bl_label = "Prepare for UE5 Export"
    bl_description = ('Applies object transforms to bone data and removes '
                      'object-level animation. Fixes flipped/oversized import in UE5. '
                      'After this, export FBX with Forward=-Y, Up=Z.')
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
                                  'Now export FBX: Forward=-Y, Up=Z, Scale=1.0')
        else:
            self.report({'INFO'}, 'Armature already prepared. Export FBX: Forward=-Y, Up=Z, Scale=1.0')

        return {'FINISHED'}


class ExportFBXForUE5(bpy.types.Operator):
    """Export the selected armature as FBX with correct settings for Unreal Engine 5.

    This operator automates the entire export workflow without modifying the
    original armature.

    Pipeline:
      1. Duplicate the armature + child meshes
      2. Apply object transforms (bakes armature rotation into bone rest poses)
      3. Remove object-level animation fcurves
      4. Export FBX with bake_space_transform=True (handles axis conversion)
      5. Clean up temporary copies

    ROOT MOTION STRATEGY:
    Root motion stays on the BONE (e.g. mixamorig:Hips). The armature is
    exported as armature_nodetype='NULL' so UE5 treats the topmost actual
    bone as the root bone and extracts root motion from its location.

    WHY transform_apply + NO _correct_root_bone_location:
    After transform_apply, the armature is at identity and bone rest poses
    are in Blender world space. However, bone LOCATION keyframes for the
    parentless root bone remain in the OLD armature space. We do NOT correct
    them, because:

      With correction: visual = R(90°,X) @ (old_head + old_loc)
                       FBX = R(-90°,X) @ visual = old_head + old_loc (NO conversion!)

      Without correction: visual = R(90°,X) @ old_head + old_loc
                         FBX = R(-90°,X) @ visual = old_head + R(-90°,X) @ old_loc
                         For old_loc=(0,-7,0): delta=(0,0,7) → +Z forward in FBX ✓

    The uncorrected location keyframes, when processed by bake_space_transform,
    produce the correct FBX output because R(-90°,X) converts the root bone's
    Blender-space delta to FBX-space delta.
    """
    bl_idname = "rsl.export_fbx_ue5"
    bl_label = "Export FBX"
    bl_description = ('One-click export to UE5-ready FBX with correct root '
                      'motion and axis conversion for Unreal Engine 5.')
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

        print(f'RSL Export v2.2.0: Starting UE5 FBX export for "{armature.name}"')

        # --- Step 1: Select armature + child meshes and duplicate together ---
        bpy.ops.object.select_all(action='DESELECT')
        armature.select_set(True)
        child_meshes = []
        for child in armature.children:
            if child.type == 'MESH':
                child.select_set(True)
                child_meshes.append(child)

        bpy.ops.object.duplicate_move(OBJECT_OT_duplicate={"linked": False})

        armature_copy = context.active_object
        mesh_copies = [obj for obj in context.selected_objects if obj.type == 'MESH']

        # --- Step 2: Apply ALL transforms on the copies ---
        # This bakes the armature's object-level rotation (e.g. 90° X for Mixamo)
        # into the bone rest poses. After this, the armature is at identity.
        #
        # This is CRITICAL for two reasons:
        #
        # 1) Without this step, bake_space_transform would compute:
        #    space_transform = axis_conversion @ armature_rotation
        #    = R(-90°,X) @ R(90°,X) = Identity
        #    Result: NO axis conversion, bone data stays in Blender space.
        #
        # 2) After transform_apply, the bone LOCATION keyframes are NOT adjusted
        #    by Blender. For the parentless root bone, bone.location is in armature
        #    space, which is now identity (world space). The old location values
        #    remain in the OLD armature space.
        #
        #    We do NOT correct these values (no _correct_root_bone_location).
        #    Instead, we let bake_space_transform handle the conversion:
        #
        #    Visual position = R(90°,X) @ old_head + old_loc
        #    FBX position = R(-90°,X) @ visual = old_head + R(-90°,X) @ old_loc
        #
        #    For old_loc = (0, -7, 0) (walking forward in Blender):
        #    R(-90°,X) @ (0, -7, 0) = (0, 0, 7) → +Z in FBX → forward ✓
        #
        #    If we corrected old_loc to R(90°,X) @ old_loc first:
        #    Visual = R(90°,X) @ old_head + R(90°,X) @ old_loc = R(90°,X) @ (old_head + old_loc)
        #    FBX = R(-90°,X) @ R(90°,X) @ (old_head + old_loc) = old_head + old_loc
        #    = Blender armature space → NO conversion → character rotated 90° in UE5 ✗
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature_copy)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        for mesh in mesh_copies:
            bpy.ops.object.select_all(action='DESELECT')
            utils.set_active(mesh)
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # --- Step 3: Remove ALL object-level animation fcurves ---
        # After transform_apply, the armature is at identity, so any remaining
        # object-level keyframes would be at or near zero. Remove them to prevent
        # the FBX exporter from including unnecessary armature-level animation.
        if armature_copy.animation_data and armature_copy.animation_data.action:
            fcurves_to_remove = []
            for fcurve in armature_copy.animation_data.action.fcurves:
                if fcurve.data_path in ('location', 'rotation_euler', 'rotation_quaternion',
                                        'rotation_axis_angle', 'scale'):
                    fcurves_to_remove.append(fcurve)
            for fcurve in fcurves_to_remove:
                armature_copy.animation_data.action.fcurves.remove(fcurve)
            if fcurves_to_remove:
                print(f'RSL Export: Removed {len(fcurves_to_remove)} object-level fcurves')

        # Remove NLA tracks on copy
        if armature_copy.animation_data:
            for track in list(armature_copy.animation_data.nla_tracks):
                armature_copy.animation_data.nla_tracks.remove(track)

        # Remove orphaned actions
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

        # --- Step 4: Select all copies for export ---
        bpy.ops.object.select_all(action='DESELECT')
        armature_copy.select_set(True)
        for mesh in mesh_copies:
            mesh.select_set(True)

        # --- Step 5: Export FBX ---
        # After step 2 (transform_apply), the armature is at identity. Bone rest
        # poses are in Blender world space, but root bone location keyframes are
        # still in the OLD armature space (because Blender doesn't adjust them).
        #
        # This is INTENTIONAL. We let bake_space_transform handle the conversion:
        #   Visual position = R(90°,X) @ old_head + old_loc
        #   FBX position = R(-90°,X) @ visual = old_head + R(-90°,X) @ old_loc
        #   Root bone delta: R(-90°,X) @ (0, -7, 0) = (0, 0, 7) → +Z forward in FBX ✓
        #
        # We export with standard Blender axis settings (axis_forward='-Y', axis_up='Z')
        # and bake_space_transform=True. Since the armature is at identity, the
        # combined space transform is just the axis conversion (R(-90°, X)), which
        # correctly converts bone data from Blender space to FBX space:
        #   Blender -Y (forward) → FBX +Z (forward) → UE5 +X (forward)
        #   Blender +Z (up)      → FBX +Y (up)      → UE5 +Z (up)
        #
        # With armature_nodetype='NULL', UE5 treats the topmost bone as the root
        # bone and extracts root motion from its location keyframes.
        export_params = dict(
            filepath=filepath,
            use_selection=True,
            global_scale=1.0,
            apply_scale_options='FBX_SCALE_ALL',
            axis_forward='-Y',
            axis_up='Z',
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
            bake_space_transform=True,
            object_types={'ARMATURE', 'MESH'},
        )

        try:
            bpy.ops.export_scene.fbx(**export_params)
            print(f'RSL Export FBX for UE5: Exported to "{filepath}"')
        except TypeError:
            # Fallback: some Blender versions don't support all params
            fallback_params = dict(
                filepath=filepath,
                use_selection=True,
                global_scale=1.0,
                axis_forward='-Y',
                axis_up='Z',
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
                bake_space_transform=True,
                object_types={'ARMATURE', 'MESH'},
            )
            try:
                bpy.ops.export_scene.fbx(**fallback_params)
                print(f'RSL Export FBX (compat mode): Exported to "{filepath}"')
            except Exception as e:
                self.report({'ERROR'}, f'FBX export failed: {e}')
                self._cleanup(armature_copy, mesh_copies)
                return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f'FBX export failed: {e}')
            self._cleanup(armature_copy, mesh_copies)
            return {'CANCELLED'}

        # --- Step 6: Clean up ---
        self._cleanup(armature_copy, mesh_copies)

        # Restore original selection
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature)
        armature.select_set(True)

        self.report({'INFO'}, f'FBX exported for UE5: {filepath}')
        return {'FINISHED'}

    @staticmethod
    def _correct_root_bone_location(armature, saved_rot_scale):
        """
        Correct the parentless root bone's location keyframes after transform_apply.

        After transform_apply(rotation+scale), the bone rest poses absorb the
        armature's rotation and scale, but the bone's LOCATION keyframes are NOT
        adjusted by Blender. They remain in the OLD armature space.

        For a parentless bone, bone.location is in armature space. After apply,
        the new armature space is aligned with world space (identity). The old
        values need to be transformed by the armature's old rotation+scale matrix:

            new_bone_location = saved_rot_scale @ old_bone_location

        This preserves the visual position because:
            old_visual = R@S @ (head + loc) + L = R@S@head + R@S@loc + L
            new_visual = (R@S@head + L) + R@S@loc = same ✓

        Parameters:
          armature: The armature (after transform_apply, at identity)
          saved_rot_scale: The 3x3 rotation+scale matrix saved BEFORE apply
        """
        if not armature.animation_data or not armature.animation_data.action:
            print('RSL Export: No animation data - skipping location correction')
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

        data_path = f'pose.bones["{root_bone_name}"].location'

        # Collect the 3 location fcurves
        loc_fcurves = [None, None, None]
        for fcurve in action.fcurves:
            if fcurve.data_path == data_path:
                if 0 <= fcurve.array_index <= 2:
                    loc_fcurves[fcurve.array_index] = fcurve

        if not any(loc_fcurves):
            print(f'RSL Export: No location fcurves for "{root_bone_name}" - skipping')
            return

        # Read all keyframe values from the 3 location fcurves
        frame_data = {}  # {frame: [x, y, z]}
        for axis in range(3):
            fc = loc_fcurves[axis]
            if fc:
                for kp in fc.keyframe_points:
                    frame = int(round(kp.co.x))
                    if frame not in frame_data:
                        frame_data[frame] = [0.0, 0.0, 0.0]
                    frame_data[frame][axis] = kp.co.y

        if not frame_data:
            print(f'RSL Export: No location keyframes for "{root_bone_name}" - skipping')
            return

        # Transform each frame's location by the saved rotation+scale matrix
        from mathutils import Vector
        max_displacement = 0.0
        first_loc = None
        last_loc = None
        for frame in sorted(frame_data.keys()):
            old_loc = Vector(frame_data[frame])
            new_loc = saved_rot_scale @ old_loc
            frame_data[frame] = new_loc

            if first_loc is None:
                first_loc = new_loc.copy()
            last_loc = new_loc.copy()

        if first_loc and last_loc:
            delta = last_loc - first_loc
            max_displacement = delta.length
            print(f'RSL Export: Root bone "{root_bone_name}" location delta: {delta}')
            print(f'RSL Export: Delta length: {delta.length:.4f}')
            # Diagnostic: warn if walking motion is primarily vertical (axis issue)
            abs_vals = [abs(delta.x), abs(delta.y), abs(delta.z)]
            dominant_axis = ['X', 'Y', 'Z'][abs_vals.index(max(abs_vals))]
            if dominant_axis == 'Z' and abs_vals[2] > 0.01:
                # In Blender world space (after _correct_root_bone_location),
                # walking should be along Y (forward), Z is up.
                # If Z dominates, the motion may be mapped to vertical in UE5.
                print(f'RSL Export: WARNING - Walking delta is primarily along {dominant_axis} '
                      f'({delta}). After FBX export, this should map to UE5 forward (+X). '
                      f'If the character walks underground in UE5, check axis settings.')
            elif dominant_axis == 'Y' and abs_vals[1] > 0.01:
                print(f'RSL Export: Walking delta is along Y (Blender forward) - '
                      f'correct direction for axis_forward=-Y export')

        # Remove old fcurves
        for fc in loc_fcurves:
            if fc:
                action.fcurves.remove(fc)

        # Create new fcurves with corrected values
        new_fcurves = {}
        for axis in range(3):
            fc = action.fcurves.new(data_path=data_path, index=axis)
            new_fcurves[axis] = fc

        # Populate with transformed values
        for frame in sorted(frame_data.keys()):
            loc = frame_data[frame]
            for axis in range(3):
                new_fcurves[axis].keyframe_points.insert(frame, loc[axis])

        # Update fcurves
        for fc in new_fcurves.values():
            fc.update()

        # Update the bone's current location
        root_bone = armature.pose.bones[root_bone_name]
        min_frame = min(frame_data.keys())
        root_bone.location = frame_data[min_frame]

        print(f'RSL Export: Root bone "{root_bone_name}" location corrected '
              f'({len(frame_data)} keyframes, displacement={max_displacement:.4f})')
        print(f'RSL Export: First frame loc: {first_loc}')
        print(f'RSL Export: Last frame loc: {last_loc}')

    @staticmethod
    def _transfer_root_motion_to_armature(armature):
        """
        Transfer root motion from the parentless root bone to the armature OBJECT.

        After _correct_root_bone_location, the bone's location keyframes are in
        the new armature space (identity after transform_apply). We transfer the
        walking delta (relative to frame 1) to the armature object's location,
        and set the bone's location to the frame-1 reference value (constant).

        This preserves the visual pose because:
          original_visual = armature_origin + bone_rest_head + bone_location_N
          new_visual = (armature_location_N) + bone_rest_head + bone_ref_location
          where armature_location_N = bone_location_N - bone_ref_location
          So: new_visual = (bone_location_N - ref) + bone_rest_head + ref = original ✓

        The FBX exporter applies axis conversion to OBJECT animation, so the
        armature object's walking delta gets correctly converted to FBX/UE5 space.
        With axis_forward='-Y', axis_up='Z', the conversion is identity, so
        walking in -Y maps directly to UE5 forward (+X).
        """
        if not armature.animation_data or not armature.animation_data.action:
            print('RSL Export: No animation data - skipping root motion transfer')
            return

        action = armature.animation_data.action

        # Find the parentless root bone
        root_bone_name = ''
        for bone in armature.pose.bones:
            if not bone.parent:
                root_bone_name = bone.name
                break

        if not root_bone_name:
            print('RSL Export: No parentless root bone - skipping root motion transfer')
            return

        bone_data_path = f'pose.bones["{root_bone_name}"].location'

        # Read the bone's corrected location keyframes
        bone_loc_fcurves = [None, None, None]
        for fcurve in action.fcurves:
            if fcurve.data_path == bone_data_path:
                if 0 <= fcurve.array_index <= 2:
                    bone_loc_fcurves[fcurve.array_index] = fcurve

        if not any(bone_loc_fcurves):
            print(f'RSL Export: No location fcurves for "{root_bone_name}" - skipping transfer')
            return

        # Collect all keyframe data (frame -> [x, y, z])
        from mathutils import Vector
        bone_frame_data = {}
        for axis in range(3):
            fc = bone_loc_fcurves[axis]
            if fc:
                for kp in fc.keyframe_points:
                    frame = int(round(kp.co.x))
                    if frame not in bone_frame_data:
                        bone_frame_data[frame] = [0.0, 0.0, 0.0]
                    bone_frame_data[frame][axis] = kp.co.y

        if not bone_frame_data:
            print(f'RSL Export: No bone location keyframes - skipping transfer')
            return

        # Get the first frame's location as the reference point
        sorted_frames = sorted(bone_frame_data.keys())
        ref_frame = sorted_frames[0]
        ref_loc = Vector(bone_frame_data[ref_frame])

        # Compute the walking delta for each frame
        delta_data = {}  # frame -> Vector (delta from reference)
        max_delta = 0.0
        for frame in sorted_frames:
            delta = Vector(bone_frame_data[frame]) - ref_loc
            delta_data[frame] = delta
            if delta.length > max_delta:
                max_delta = delta.length

        if max_delta < 0.001:
            print(f'RSL Export: Root bone has no significant walking motion '
                  f'(max delta={max_delta:.6f}) - skipping transfer')
            return

        print(f'RSL Export: Transferring root motion from bone "{root_bone_name}" '
              f'to armature object (max delta={max_delta:.4f})')

        # Switch to object mode to set armature object keyframes
        bpy.ops.object.mode_set(mode='OBJECT')

        # Set armature object location keyframes (the walking delta)
        for frame in sorted_frames:
            delta = delta_data[frame]
            armature.location = delta
            armature.keyframe_insert(data_path='location', frame=frame)

        # Now set the bone's location to the reference value (constant)
        # This preserves the visual pose: armature_delta + bone_ref = original_bone_location
        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature)
        bpy.ops.object.mode_set(mode='POSE')

        root_bone = armature.pose.bones[root_bone_name]
        root_bone.location = ref_loc

        # Remove old bone location fcurves and create new ones with constant ref value
        for fc in bone_loc_fcurves:
            if fc:
                action.fcurves.remove(fc)

        # Create new bone location fcurves with the reference value (constant)
        for axis in range(3):
            fc = action.fcurves.new(data_path=bone_data_path, index=axis)
            for frame in sorted_frames:
                fc.keyframe_points.insert(frame, ref_loc[axis])
            fc.update()

        bpy.ops.object.mode_set(mode='OBJECT')

        # Verify the transfer
        arm_loc_fcurves = []
        for fcurve in action.fcurves:
            if fcurve.data_path == 'location':
                arm_loc_fcurves.append(fcurve)

        print(f'RSL Export: Armature object now has {len(arm_loc_fcurves)} location fcurves')
        print(f'RSL Export: Root bone "{root_bone_name}" location set to constant: {ref_loc}')
        print(f'RSL Export: Walking delta at last frame: {delta_data[sorted_frames[-1]]}')

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

        # ============================================================
        # PRECOMPILE ROOT MOTION — BEFORE ANY MODIFICATIONS
        # ============================================================
        # This is the ONLY reliable way to get root motion: sample the
        # source armature's world-space hip position BEFORE we modify
        # anything (before T-pose apply, before transfer_armature_location_to_hip,
        # before clean_animation, before copy_rest_pose, before transform_apply).
        #
        # All previous approaches failed because they tried to read root motion
        # AFTER modifications had already destroyed or altered the data:
        #   - COPY_LOCATION WORLD/WORLD doesn't bake correctly with NLA bake
        #   - HIPS location fcurves are stripped during bake (not in root_bones)
        #   - Source armature location fcurves are deleted before PASS 2/3
        #   - Depsgraph evaluation after constraints removed shows rest pose
        #
        # By sampling HERE, we get the ground truth walking motion that will
        # be applied to the ROOT bone after retargeting is complete.
        precompiled_root_motion = None
        if root_motion_bones:
            precompiled_root_motion = self.precompile_root_motion(
                armature_source, armature_target, root_motion_bones, root_bones)
            if precompiled_root_motion:
                print(f'RSL Root Motion: Precompiled {len(precompiled_root_motion)} frames '
                      f'of walking delta (BEFORE any modifications)')
            else:
                print('RSL Root Motion: WARNING — Failed to precompile root motion')

        # --- T-Pose Reference: Apply before retargeting ---
        tpose_applied = False
        tpose_ref = context.scene.rsl_retargeting_tpose_reference
        if tpose_ref and context.scene.rsl_retargeting_tpose_apply_before:
            if (tpose_ref.type == 'ARMATURE' and
                    armature_source.animation_data and armature_source.animation_data.action):
                count = ApplyTPoseReference.apply_tpose(tpose_ref, armature_source, frame=1)
                tpose_applied = True
                print(f'RSL: T-Pose reference applied before retargeting ({count} bones)')

        # Transfer armature-level location data to hip bone.
        # This is still needed so that the COPY_TRANSFORMS constraint on the
        # source copy armature sees the correct visual pose during bake.
        self.transfer_armature_location_to_hip(armature_source, root_motion_bones)

        # Remove the armature-level location fcurves after transfer.
        # This prevents double-movement during the retargeting process.
        if armature_source.animation_data and armature_source.animation_data.action:
            fcurves_to_remove = []
            for fcurve in armature_source.animation_data.action.fcurves:
                if fcurve.data_path == 'location':
                    fcurves_to_remove.append(fcurve)
            for fcurve in fcurves_to_remove:
                armature_source.animation_data.action.fcurves.remove(fcurve)

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

            # Add COPY_LOCATION ONLY for parentless root bones that are in the
            # retarget list. Do NOT add COPY_LOCATION for hip/root motion bones
            # (e.g. pelvis) because COPY_LOCATION WORLD/WORLD is unreliable
            # during NLA bake — it often produces zero location keyframes.
            # Root motion is handled separately after baking.
            if bone_target.name in root_bones:
                constraint = bone_target.constraints.new('COPY_LOCATION')
                constraint.name += RETARGET_ID
                constraint.target = armature_source
                constraint.subtarget = item.bone_name_source
                # Explicitly set spaces for reliable location copying
                constraint.target_space = 'WORLD'
                constraint.owner_space = 'WORLD'
                print(f'RSL: COPY_LOCATION added for root bone "{bone_target.name}" -> source "{item.bone_name_source}" (WORLD/WORLD)')

            # Select the bone for animation
            armature_target.data.bones.get(item.bone_name_target).select = True

        # Also select the parentless ROOT bone for baking, even if it's not
        # in the retarget list. Without selection, it won't get baked and
        # extract_root_motion_to_root_bone() can't add location keyframes to it.
        for bone in armature_target.pose.bones:
            if not bone.parent:
                armature_target.data.bones.get(bone.name).select = True

        # Bake the animation to the target armature
        # Only pass parentless root bones for location preservation.
        # Hip/root motion bones are NOT baked via COPY_LOCATION (unreliable),
        # so their location is handled separately below.
        self.bake_animation(armature_source, armature_target, root_bones)

        # Remove constraints from target armature BEFORE root motion processing.
        for bone in armature_target.pose.bones:
            for constraint in bone.constraints:
                if RETARGET_ID in constraint.name:
                    bone.constraints.remove(constraint)

        # --- Root Motion: Apply precompiled walking delta to ROOT bone ---
        # We already sampled the source armature's world-space hip position
        # BEFORE any modifications. Now we apply that delta to the ROOT bone.
        if root_motion_bones and precompiled_root_motion:
            self.apply_precompiled_root_motion(
                armature_target, root_motion_bones, root_bones,
                precompiled_root_motion,
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

        bpy.ops.object.select_all(action='DESELECT')
        utils.set_active(armature_target)

        # After retargeting, the armature is at identity with all bone data in world space.
        # We keep the armature at identity (do NOT restore the old rotation) because:
        #   1. The initial transform_apply baked the rotation into bone rest poses
        #   2. Restoring rotation + transform_apply causes double-rotation (R² instead of R)
        #   3. Location keyframes would need manual adjustment that's error-prone
        # The armature at identity means the character appears correctly upright in Blender.
        # The FBX export operator (ExportFBXForUE5) handles axis conversion separately.
        armature_target.location = location
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

        # Get the armature's base (non-animated) location so we can compute deltas.
        # The fcurve values are ABSOLUTE positions, not relative offsets.
        # We need the DELTA from the rest position to get the correct displacement.
        arm_base_location = armature_source.location.copy()

        for frame in all_frames:
            bpy.context.scene.frame_set(frame)

            # Read the armature object's animated location at this frame
            arm_location_animated = mathutils.Vector((0, 0, 0))
            for axis, keyframes in location_data.items():
                # Evaluate the fcurve at this frame for smooth interpolation
                for fcurve in obj_location_fcurves:
                    if fcurve.array_index == axis:
                        arm_location_animated[axis] = fcurve.evaluate(frame)
                        break

            # Compute the world-space DELTA from the armature's base position.
            # This is the displacement that the bone needs to replicate.
            arm_location_delta = arm_location_animated - arm_base_location

            # Convert to parent-local space
            #
            # The armature's location delta is a world-space displacement from the
            # armature's rest position. We must convert this delta to the bone's
            # parent-local space so that setting bone.location produces the same
            # visual displacement.
            #
            # Conversion chain:
            #   delta_armature = S_inv @ R_inv @ delta_world   (world → armature space)
            #   delta_parent   = R_parent_inv  @ delta_armature (armature → parent-local)
            #
            # CRITICAL: We must use 3x3 rotation matrices (direction/vector transform)
            # NOT 4x4 with homogeneous coordinate 1 (point transform). Using 4x4 with
            # w=1 incorrectly adds the parent bone's translation offset, which causes:
            #   - Root bone staying stationary (offset cancels the motion)
            #   - Character flipping/moving in wrong direction when parent has rotation
            if hip_bone.parent:
                parent_rot_inv = hip_bone.parent.bone.matrix_local.to_3x3().inverted()
                arm_rot_mat_inv = arm_rot_euler.to_matrix().inverted()
                arm_scale_mat_inv = mathutils.Matrix.Diagonal((1/arm_scale.x, 1/arm_scale.y, 1/arm_scale.z))

                # Convert world-space delta to armature-local space
                delta_armature = arm_scale_mat_inv @ arm_rot_mat_inv @ arm_location_delta
                # Convert armature-space delta to parent-local space (direction only, no translation)
                delta_parent_local = parent_rot_inv @ delta_armature
                new_bone_location = rest_head_parent_local + delta_parent_local
            else:
                # No parent: bone location is in armature-local space directly.
                # For a parentless bone, bone.location is in "channel space" that
                # gets rotated by the bone's rest pose rotation. We must undo this
                # rotation so that the visual position is correct:
                #   visual = matrix_local @ Translation(bone.location)
                #   We want: matrix_local @ Translation(new_loc) = visual_target
                #   So: new_loc = R_rest_inv @ delta_armature
                arm_rot_mat_inv = arm_rot_euler.to_matrix().inverted()
                arm_scale_mat_inv = mathutils.Matrix.Diagonal((1/arm_scale.x, 1/arm_scale.y, 1/arm_scale.z))
                delta_armature = arm_scale_mat_inv @ arm_rot_mat_inv @ arm_location_delta

                bone_rest_rot_inv = hip_bone.bone.matrix_local.to_3x3().inverted()
                delta_channel = bone_rest_rot_inv @ delta_armature
                new_bone_location = rest_head_parent_local + delta_channel

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

    def precompile_root_motion(self, armature_source, armature_target,
                                root_motion_bones, root_bones):
        """
        Sample the source armature's world-space hip position BEFORE any modifications.

        This is the ONLY reliable way to capture root motion. By sampling here
        (before T-pose apply, before transfer_armature_location_to_hip, before
        clean_animation, before copy_rest_pose, before transform_apply), we get
        the ground truth walking motion.

        Returns: dict {frame: mathutils.Vector(delta_from_frame_0)} or None
        """
        rm_source = list(root_motion_bones.values())[0]
        source_hip = armature_source.pose.bones.get(rm_source)

        if not source_hip:
            print(f'RSL Root Motion Precompile: Source hip bone "{rm_source}" not found')
            return None

        if not armature_source.animation_data or not armature_source.animation_data.action:
            print('RSL Root Motion Precompile: Source armature has no animation')
            return None

        source_action = armature_source.animation_data.action

        # Get frame range from the source action
        frame_start = None
        frame_end = None
        for fcurve in source_action.fcurves:
            for kp in fcurve.keyframe_points:
                f = kp.co.x
                if frame_start is None or f < frame_start:
                    frame_start = f
                if frame_end is None or f > frame_end:
                    frame_end = f

        if frame_start is None or frame_end is None:
            print('RSL Root Motion Precompile: No keyframes found in source action')
            return None

        frame_start = int(frame_start)
        frame_end = int(frame_end)
        frame_range = list(range(frame_start, frame_end + 1))

        print(f'RSL Root Motion Precompile: Sampling frames {frame_start}-{frame_end} '
              f'({len(frame_range)} frames) for hip bone "{rm_source}"')

        # Switch to object mode for depsgraph evaluation
        bpy.context.view_layer.objects.active = armature_source
        if armature_source.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Sample world-space hip position at each frame
        hips_world_per_frame = {}
        for frame in frame_range:
            bpy.context.scene.frame_set(frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            src_eval = armature_source.evaluated_get(depsgraph)
            src_hip = src_eval.pose.bones.get(rm_source)
            if src_hip:
                hips_world = src_eval.matrix_world @ src_hip.matrix.translation
                hips_world_per_frame[frame] = hips_world.copy()

        if not hips_world_per_frame:
            print('RSL Root Motion Precompile: No positions sampled')
            return None

        # Compute walking delta (displacement from first frame)
        sorted_frames = sorted(hips_world_per_frame.keys())
        rest_pos = hips_world_per_frame[sorted_frames[0]].copy()

        delta_per_frame = {}
        max_delta = 0.0
        for frame in sorted_frames:
            delta = hips_world_per_frame[frame] - rest_pos
            delta_per_frame[frame] = delta
            if delta.length > max_delta:
                max_delta = delta.length

        if max_delta < 0.001:
            print(f'RSL Root Motion Precompile: No significant motion detected '
                  f'(max_delta={max_delta:.6f}). Animation may not have walking.')
            return None

        print(f'RSL Root Motion Precompile: SUCCESS — max delta = {max_delta:.4f}')
        last_frame = sorted_frames[-1]
        print(f'  Walking delta at last frame: {delta_per_frame[last_frame]}')

        return delta_per_frame

    def apply_precompiled_root_motion(self, armature_target, root_motion_bones, root_bones,
                                       precompiled_delta, keep_offset, root_motion_rest_offsets):
        """
        Apply precompiled root motion delta to the parentless ROOT bone.

        The delta is in world space (from the source armature's coordinate system).
        We need to convert it to the target ROOT bone's channel space.

        After transform_apply on the target armature, the armature is at identity,
        so world space = armature space. The ROOT bone's channel space is related
        to its rest pose rotation:
          visual_position = matrix_local @ Translation(channel_location)
          So: channel_location = R_rest_inv @ delta_world

        We also zero out the HIPS location to prevent double-movement.
        """
        if not armature_target.animation_data or not armature_target.animation_data.action:
            print('RSL Root Motion Apply: No animation data on target armature')
            return

        target_action = armature_target.animation_data.action

        # Find the parentless ROOT bone
        root_bone_name = ''
        root_bone = None
        for bone in armature_target.pose.bones:
            if not bone.parent:
                root_bone_name = bone.name
                root_bone = bone
                break

        if not root_bone:
            print('RSL Root Motion Apply: No parentless root bone found')
            return

        # Find the HIPS bone
        hips_bone_name = ''
        hips_bone = None
        for rm_target in root_motion_bones:
            hips_bone_name = rm_target
            hips_bone = armature_target.pose.bones.get(rm_target)
            break

        if not hips_bone:
            print(f'RSL Root Motion Apply: HIPS bone "{hips_bone_name}" not found')
            return

        if root_bone_name == hips_bone_name:
            print(f'RSL Root Motion Apply: Root and HIPS are the same bone — no extraction needed')
            return

        print(f'RSL Root Motion Apply: ROOT="{root_bone_name}", HIPS="{hips_bone_name}"')
        print(f'  Applying {len(precompiled_delta)} precompiled deltas')

        # Get the ROOT bone's rest pose rotation (for channel space conversion)
        # After transform_apply, the armature is at identity, so the rest pose
        # is in world space. The bone's channel space is rotated by its rest rotation.
        root_rest_rot_inv = root_bone.bone.matrix_local.to_3x3().inverted()

        # Switch to pose mode for keyframe insertion
        bpy.context.view_layer.objects.active = armature_target
        if armature_target.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='POSE')

        for frame in sorted(precompiled_delta.keys()):
            delta_world = precompiled_delta[frame]

            # Convert world-space delta to ROOT bone's channel space
            # channel_loc = R_rest_inv @ delta_world
            delta_channel = root_rest_rot_inv @ delta_world

            # Set ROOT bone location
            root_bone.location = delta_channel
            root_bone.keyframe_insert(data_path='location', frame=frame)

            # Zero out HIPS location to prevent double-movement
            hips_bone.location = (0, 0, 0)
            hips_bone.keyframe_insert(data_path='location', frame=frame)

        bpy.ops.object.mode_set(mode='OBJECT')

        # Clean up: remove HIPS location fcurves that are all zeros
        fcurves_to_check = []
        for fcurve in target_action.fcurves:
            if fcurve.data_path == f'pose.bones["{hips_bone_name}"].location':
                fcurves_to_check.append(fcurve)

        for fcurve in fcurves_to_check:
            all_zero = all(abs(kp.co.y) < 0.0001 for kp in fcurve.keyframe_points)
            if all_zero:
                target_action.fcurves.remove(fcurve)

        # Verify ROOT has location animation
        root_loc_fcurves = []
        for fcurve in target_action.fcurves:
            if fcurve.data_path == f'pose.bones["{root_bone_name}"].location':
                root_loc_fcurves.append(fcurve)

        if len(root_loc_fcurves) >= 3:
            has_movement = False
            max_spread = 0.0
            for fc in root_loc_fcurves:
                values = [kp.co.y for kp in fc.keyframe_points]
                if len(values) > 1:
                    spread = abs(max(values) - min(values))
                    max_spread = max(max_spread, spread)
                    if spread > 0.001:
                        has_movement = True

            if has_movement:
                print(f'RSL Root Motion Apply: SUCCESS — ROOT "{root_bone_name}" has '
                      f'location animation (max spread={max_spread:.4f})')
            else:
                print(f'RSL Root Motion Apply: WARNING — ROOT "{root_bone_name}" has no '
                      f'significant movement (max spread={max_spread:.6f})')
        else:
            print(f'RSL Root Motion Apply: WARNING — ROOT has only '
                  f'{len(root_loc_fcurves)} location fcurves (expected 3)')

        # Apply rest pose offset if needed
        if keep_offset and root_motion_rest_offsets:
            self.apply_root_motion_offset(armature_target, root_motion_rest_offsets)

    def extract_hips_motion_to_root(self, armature_target, armature_source_original,
                                     root_motion_bones, root_bones,
                                     keep_offset, root_motion_rest_offsets):
        """
        Extract root motion and apply it to the parentless ROOT bone.

        Three-pass strategy:
        PASS 1: Read baked HIPS location fcurves from TARGET action.
                Works if COPY_LOCATION baked correctly.
        PASS 2: Sample SOURCE HIPS via depsgraph.
                Works if source armature evaluates correctly.
        PASS 3: Read SOURCE hip bone location fcurves DIRECTLY.
                This ALWAYS works — just reads stored keyframe data.
                No depsgraph, no constraints, no bake involved.

        After getting the walking delta, we:
        1. Apply delta to the parentless ROOT bone (channel space)
        2. Zero out HIPS location so motion isn't double-applied
        """
        if not armature_target.animation_data or not armature_target.animation_data.action:
            print('RSL Root Motion: No animation data on target armature')
            return

        target_action = armature_target.animation_data.action

        # Find the parentless ROOT bone
        root_bone_name = ''
        root_bone = None
        for bone in armature_target.pose.bones:
            if not bone.parent:
                root_bone_name = bone.name
                root_bone = bone
                break

        if not root_bone:
            print('RSL Root Motion: No parentless root bone found in target armature')
            return

        # Find the HIPS bone from root_motion_bones
        hips_bone_name = ''
        hips_bone = None
        for rm_target in root_motion_bones:
            hips_bone_name = rm_target
            hips_bone = armature_target.pose.bones.get(rm_target)
            break

        if not hips_bone:
            print(f'RSL Root Motion: HIPS bone "{hips_bone_name}" not found')
            return

        if root_bone_name == hips_bone_name:
            print(f'RSL Root Motion: Root and HIPS are the same bone "{root_bone_name}" - no extraction needed')
            return

        # Get source HIPS bone name
        rm_source = list(root_motion_bones.values())[0]

        print(f'RSL Root Motion: ROOT="{root_bone_name}", HIPS="{hips_bone_name}", '
              f'Source HIPS="{rm_source}"')

        # --- Get frame range from the target action ---
        all_frames = set()
        for fcurve in target_action.fcurves:
            for kp in fcurve.keyframe_points:
                all_frames.add(int(round(kp.co.x)))
        all_frames = sorted(all_frames)

        if not all_frames:
            print('RSL Root Motion: No keyframes found in target action')
            return

        frame_start = all_frames[0]
        frame_end = all_frames[-1]
        frame_range = list(range(frame_start, frame_end + 1))

        print(f'RSL Root Motion: Frame range {frame_start}-{frame_end} ({len(frame_range)} frames)')

        # ============================================================
        # PASS 1: Read baked HIPS location fcurves from TARGET action
        # ============================================================
        print('RSL Root Motion: PASS 1 - Reading TARGET HIPS location fcurves...')
        hips_loc_fcurves = [None, None, None]
        hips_data_path = f'pose.bones["{hips_bone_name}"].location'
        for fcurve in target_action.fcurves:
            if fcurve.data_path == hips_data_path and 0 <= fcurve.array_index <= 2:
                hips_loc_fcurves[fcurve.array_index] = fcurve

        delta_per_frame = {}
        max_delta = 0.0

        if any(hips_loc_fcurves):
            # Read HIPS location at each frame and compute armature-space delta
            # After transform_apply, armature is at identity, so armature space = world space
            # HIPS location is in parent (ROOT) local space
            # We need: hips_world = root_rest_mat @ (hips_rest_offset + hips_location)
            root_rest_mat = root_bone.bone.matrix_local.copy()

            if hips_bone.parent:
                parent_rest_inv = hips_bone.parent.bone.matrix_local.inverted()
                hips_rest_offset = (parent_rest_inv @ hips_bone.bone.matrix_local).translation.copy()
            else:
                hips_rest_offset = hips_bone.bone.matrix_local.translation.copy()

            # Also read ROOT rotation keyframes (they affect HIPS world position)
            root_rot_mode = root_bone.rotation_mode
            if root_rot_mode == 'QUATERNION':
                root_rot_data_path = f'pose.bones["{root_bone_name}"].rotation_quaternion'
            else:
                root_rot_data_path = f'pose.bones["{root_bone_name}"].rotation_euler'

            root_rot_fcurves = {}
            root_loc_fcurves = [None, None, None]
            for fcurve in target_action.fcurves:
                if fcurve.data_path == root_rot_data_path:
                    root_rot_fcurves[fcurve.array_index] = fcurve
                if fcurve.data_path == f'pose.bones["{root_bone_name}"].location' and 0 <= fcurve.array_index <= 2:
                    root_loc_fcurves[fcurve.array_index] = fcurve

            hips_world_per_frame = {}
            for frame in frame_range:
                # Read HIPS location at this frame
                hips_loc = mathutils.Vector((0, 0, 0))
                for axis in range(3):
                    fc = hips_loc_fcurves[axis]
                    if fc:
                        hips_loc[axis] = fc.evaluate(frame)

                # Read ROOT location at this frame
                root_loc = mathutils.Vector((0, 0, 0))
                for axis in range(3):
                    fc = root_loc_fcurves[axis]
                    if fc:
                        root_loc[axis] = fc.evaluate(frame)

                # Read ROOT rotation at this frame
                root_rot_mat = mathutils.Matrix.Identity(4)
                if root_rot_mode == 'QUATERNION':
                    q = mathutils.Quaternion((1, 0, 0, 0))
                    for idx, fc in root_rot_fcurves.items():
                        q[idx] = fc.evaluate(frame)
                    root_rot_mat = q.to_matrix().to_4x4()
                else:
                    e = mathutils.Euler((0, 0, 0), root_rot_mode)
                    for idx, fc in root_rot_fcurves.items():
                        e[idx] = fc.evaluate(frame)
                    root_rot_mat = e.to_matrix().to_4x4()

                # Compute HIPS world position
                root_mat = root_rest_mat @ mathutils.Matrix.Translation(root_loc) @ root_rot_mat
                if hips_bone.parent:
                    hips_pos_parent = hips_rest_offset + hips_loc
                    hips_world = (root_mat @ mathutils.Vector(list(hips_pos_parent) + [1.0])).xyz.copy()
                else:
                    hips_world = hips_rest_offset + hips_loc

                hips_world_per_frame[frame] = hips_world

            delta_per_frame, max_delta = self._compute_walking_delta(hips_world_per_frame)
            print(f'  PASS 1: max_delta = {max_delta:.4f}')
        else:
            print('  PASS 1: No HIPS location fcurves found in target action')

        # ============================================================
        # PASS 2: Sample SOURCE HIPS via depsgraph
        # ============================================================
        if max_delta < 0.001 and armature_source_original:
            print('RSL Root Motion: PASS 2 - Sampling SOURCE HIPS via depsgraph...')
            bpy.context.view_layer.objects.active = armature_target
            if armature_target.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')

            hips_world_per_frame = {}
            source_hips_found = False

            for frame in frame_range:
                bpy.context.scene.frame_set(frame)
                depsgraph = bpy.context.evaluated_depsgraph_get()
                src_eval = armature_source_original.evaluated_get(depsgraph)
                src_hips = src_eval.pose.bones.get(rm_source)
                if src_hips:
                    src_hips_world = src_eval.matrix_world @ src_hips.matrix.translation
                    hips_world_per_frame[frame] = src_hips_world.copy()
                    source_hips_found = True

            if source_hips_found:
                delta_per_frame, max_delta = self._compute_walking_delta(hips_world_per_frame)
                print(f'  PASS 2: max_delta = {max_delta:.4f}')
            else:
                print(f'  PASS 2: Source HIPS bone "{rm_source}" not found')

        # ============================================================
        # PASS 3: Read SOURCE hip bone location fcurves DIRECTLY
        # This ALWAYS works — just reads stored keyframe data.
        # No depsgraph, no evaluation, no constraints.
        # ============================================================
        if max_delta < 0.001 and armature_source_original:
            print('RSL Root Motion: PASS 3 - Reading SOURCE fcurves directly...')
            source_action = None
            if armature_source_original.animation_data:
                source_action = armature_source_original.animation_data.action

            if source_action:
                # Read source hip bone location fcurves
                src_hip_path = f'pose.bones["{rm_source}"].location'
                src_hip_loc_fcurves = [None, None, None]
                for fcurve in source_action.fcurves:
                    if fcurve.data_path == src_hip_path and 0 <= fcurve.array_index <= 2:
                        src_hip_loc_fcurves[fcurve.array_index] = fcurve

                # Also read armature object location fcurves (for BVH imports)
                src_arm_loc_fcurves = [None, None, None]
                for fcurve in source_action.fcurves:
                    if fcurve.data_path == 'location' and 0 <= fcurve.array_index <= 2:
                        src_arm_loc_fcurves[fcurve.array_index] = fcurve

                has_bone_loc = any(src_hip_loc_fcurves)
                has_arm_loc = any(src_arm_loc_fcurves)

                if has_bone_loc or has_arm_loc:
                    # Get source armature transform for space conversion
                    arm_rot_euler = mathutils.Euler((0, 0, 0), 'XYZ')
                    if armature_source_original.rotation_mode == 'QUATERNION':
                        arm_rot_euler = armature_source_original.rotation_quaternion.to_euler('XYZ')
                    elif armature_source_original.rotation_mode == 'AXIS_ANGLE':
                        q = mathutils.Quaternion(
                            armature_source_original.rotation_axis_angle[1:],
                            armature_source_original.rotation_axis_angle[0])
                        arm_rot_euler = q.to_euler('XYZ')
                    else:
                        arm_rot_euler = armature_source_original.rotation_euler.copy()

                    arm_rot_mat = arm_rot_euler.to_matrix().to_4x4()
                    arm_scale = armature_source_original.scale.copy()
                    arm_scale_mat = mathutils.Matrix.Diagonal(arm_scale).to_4x4()
                    arm_base_loc = armature_source_original.location.copy()

                    # Source hip bone rest pose info
                    source_hip = armature_source_original.pose.bones.get(rm_source)
                    if source_hip:
                        if source_hip.parent:
                            parent_rest_inv = source_hip.parent.bone.matrix_local.inverted()
                            hip_rest_offset = (parent_rest_inv @ source_hip.bone.matrix_local).translation.copy()
                        else:
                            hip_rest_offset = source_hip.bone.matrix_local.translation.copy()

                        # Collect all frames from source fcurves
                        src_frames = set()
                        for fc_list in [src_hip_loc_fcurves, src_arm_loc_fcurves]:
                            for fc in fc_list:
                                if fc:
                                    for kp in fc.keyframe_points:
                                        src_frames.add(int(round(kp.co.x)))
                        # Also include target frames
                        for f in frame_range:
                            src_frames.add(f)
                        src_frames = sorted(src_frames)

                        # Also read armature rotation fcurves
                        arm_rot_fcurves = {}
                        for fcurve in source_action.fcurves:
                            if fcurve.data_path in ('rotation_euler', 'rotation_quaternion'):
                                arm_rot_fcurves[fcurve.data_path + str(fcurve.array_index)] = fcurve

                        hips_world_per_frame = {}
                        for frame in src_frames:
                            if frame < frame_start or frame > frame_end:
                                continue

                            # Read hip bone location
                            hip_loc = mathutils.Vector((0, 0, 0))
                            for axis in range(3):
                                fc = src_hip_loc_fcurves[axis]
                                if fc:
                                    hip_loc[axis] = fc.evaluate(frame)

                            # Read armature object location
                            arm_loc = mathutils.Vector((0, 0, 0))
                            for axis in range(3):
                                fc = src_arm_loc_fcurves[axis]
                                if fc:
                                    arm_loc[axis] = fc.evaluate(frame)

                            # Armature rotation at this frame
                            arm_rot_frame = arm_rot_euler.copy()
                            for key, fc in arm_rot_fcurves.items():
                                if 'rotation_euler' in key:
                                    idx = int(key[-1])
                                    arm_rot_frame[idx] = fc.evaluate(frame)
                            arm_rot_mat_frame = arm_rot_frame.to_matrix().to_4x4()

                            # Build armature world matrix
                            arm_world_loc = arm_base_loc + arm_loc
                            arm_world_mat = mathutils.Matrix.Translation(arm_world_loc) @ arm_rot_mat_frame @ arm_scale_mat

                            # Convert hip location to world space
                            if source_hip.parent:
                                # Hip location is in parent-local space
                                hip_pos_armature = hip_rest_offset + hip_loc
                            else:
                                # Hip is parentless: location is in channel space
                                hip_pos_armature = (source_hip.bone.matrix_local.to_3x3() @ hip_loc) + hip_rest_offset

                            hip_world = (arm_world_mat @ mathutils.Vector(list(hip_pos_armature) + [1.0])).xyz.copy()
                            hips_world_per_frame[frame] = hip_world

                        if hips_world_per_frame:
                            delta_per_frame, max_delta = self._compute_walking_delta(hips_world_per_frame)
                            print(f'  PASS 3: max_delta = {max_delta:.4f}')
                        else:
                            print('  PASS 3: No frames sampled')
                    else:
                        print(f'  PASS 3: Source hip bone "{rm_source}" not found in pose')
                else:
                    print('  PASS 3: No location fcurves found in source action')
            else:
                print('  PASS 3: Source armature has no action')

        # ============================================================
        # If still no motion, give up
        # ============================================================
        if max_delta < 0.001 or not delta_per_frame:
            print('RSL Root Motion: No significant walking motion detected from ANY pass')
            print('RSL Root Motion: The source animation may not have walking motion,')
            print('  or the hip bone name may be incorrect.')
            return

        print(f'RSL Root Motion: Walking delta at last frame: '
              f'{delta_per_frame.get(frame_end, "N/A")} (max={max_delta:.4f})')

        # ============================================================
        # Apply walking delta to ROOT bone
        # ============================================================
        root_rest_mat = root_bone.bone.matrix_local.copy()
        root_rest_rot = root_rest_mat.to_3x3()
        root_rest_rot_inv = root_rest_rot.inverted()

        print(f'RSL Root Motion: Applying {len(delta_per_frame)} deltas to ROOT "{root_bone_name}"')

        bpy.context.view_layer.objects.active = armature_target
        if armature_target.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='POSE')

        for frame in sorted(delta_per_frame.keys()):
            delta_world = delta_per_frame[frame]
            delta_channel = root_rest_rot_inv @ delta_world

            root_bone.location = delta_channel
            root_bone.keyframe_insert(data_path='location', frame=frame)

            # Zero out HIPS location so motion isn't double-applied
            hips_bone.location = (0, 0, 0)
            hips_bone.keyframe_insert(data_path='location', frame=frame)

        bpy.ops.object.mode_set(mode='OBJECT')

        # Clean up: remove HIPS location fcurves that are all zeros
        fcurves_to_check = []
        for fcurve in target_action.fcurves:
            if fcurve.data_path == f'pose.bones["{hips_bone_name}"].location':
                fcurves_to_check.append(fcurve)

        for fcurve in fcurves_to_check:
            all_zero = all(abs(kp.co.y) < 0.0001 for kp in fcurve.keyframe_points)
            if all_zero:
                target_action.fcurves.remove(fcurve)

        # Verify ROOT has location animation
        root_loc_fcurves = []
        for fcurve in target_action.fcurves:
            if fcurve.data_path == f'pose.bones["{root_bone_name}"].location':
                root_loc_fcurves.append(fcurve)

        if len(root_loc_fcurves) >= 3:
            has_movement = False
            max_spread = 0.0
            for fc in root_loc_fcurves:
                values = [kp.co.y for kp in fc.keyframe_points]
                if len(values) > 1:
                    spread = abs(max(values) - min(values))
                    max_spread = max(max_spread, spread)
                    if spread > 0.001:
                        has_movement = True

            if has_movement:
                print(f'RSL Root Motion: SUCCESS - ROOT "{root_bone_name}" has '
                      f'location animation (max spread={max_spread:.4f})')
            else:
                print(f'RSL Root Motion: WARNING - ROOT "{root_bone_name}" has no '
                      f'significant movement (max spread={max_spread:.6f})')
        else:
            print(f'RSL Root Motion: WARNING - ROOT has only '
                  f'{len(root_loc_fcurves)} location fcurves (expected 3)')

        # Apply rest pose offset if needed
        if keep_offset and root_motion_rest_offsets:
            self.apply_root_motion_offset(armature_target, root_motion_rest_offsets)

    def _compute_walking_delta(self, hips_world_per_frame):
        """Compute walking delta from world positions. Returns (delta_per_frame, max_delta)."""
        if not hips_world_per_frame:
            return {}, 0.0

        sorted_frames = sorted(hips_world_per_frame.keys())
        rest_frame = sorted_frames[0]
        hips_rest = hips_world_per_frame[rest_frame].copy()

        delta_per_frame = {}
        max_delta = 0.0
        for frame in sorted_frames:
            delta = hips_world_per_frame[frame] - hips_rest
            delta_per_frame[frame] = delta
            if delta.length > max_delta:
                max_delta = delta.length

        return delta_per_frame, max_delta

    def apply_root_motion_from_source_fcurves(self, armature_source_original, armature_target,
                                               root_motion_bones, root_bones,
                                               keep_offset, root_motion_rest_offsets):
        """
        Apply root motion by directly reading the SOURCE animation's hip bone
        location fcurves and computing the walking delta.

        This is the SIMPLEST and MOST RELIABLE approach because:
        1. No COPY_LOCATION constraint needed (unreliable during NLA bake)
        2. No depsgraph sampling needed (subtle evaluation issues)
        3. We read raw keyframe data directly from the source action
        4. The source animation is the ground truth for walking motion

        Algorithm:
        1. Read source hip bone location keyframes from the source action
        2. Convert source hip location to world/armature space
        3. Compute walking delta (change from first frame)
        4. Apply delta to target ROOT bone in channel space
        """
        if not armature_source_original.animation_data or not armature_source_original.animation_data.action:
            print('RSL Root Motion: Source armature has no animation data')
            return

        if not armature_target.animation_data or not armature_target.animation_data.action:
            print('RSL Root Motion: Target armature has no animation data')
            return

        source_action = armature_source_original.animation_data.action
        target_action = armature_target.animation_data.action

        # Get the source and target bone names
        rm_source = list(root_motion_bones.values())[0]
        rm_target = list(root_motion_bones.keys())[0]

        source_hip = armature_source_original.pose.bones.get(rm_source)
        if not source_hip:
            print(f'RSL Root Motion: Source hip bone "{rm_source}" not found')
            return

        print(f'RSL Root Motion: Reading fcurves from source bone "{rm_source}"')

        # --- Step 1: Read source hip bone location keyframes ---
        source_hip_path = f'pose.bones["{rm_source}"].location'
        hip_loc_fcurves = [None, None, None]
        for fcurve in source_action.fcurves:
            if fcurve.data_path == source_hip_path and 0 <= fcurve.array_index <= 2:
                hip_loc_fcurves[fcurve.array_index] = fcurve

        # Also check for armature-level location (BVH imports)
        arm_loc_fcurves = [None, None, None]
        for fcurve in source_action.fcurves:
            if fcurve.data_path == 'location' and 0 <= fcurve.array_index <= 2:
                arm_loc_fcurves[fcurve.array_index] = fcurve

        has_bone_loc = any(hip_loc_fcurves)
        has_arm_loc = any(arm_loc_fcurves)

        if not has_bone_loc and not has_arm_loc:
            print('RSL Root Motion: No location keyframes found on source hip bone or armature object')
            return

        # --- Step 2: Get the source armature's object transform ---
        # This is needed to convert bone location from armature space to world space.
        # After transfer_armature_location_to_hip(), the armature location fcurves
        # were removed, so the armature is at its base (non-animated) position.
        arm_rot_euler = mathutils.Euler((0, 0, 0), 'XYZ')
        if armature_source_original.rotation_mode == 'QUATERNION':
            arm_rot_euler = armature_source_original.rotation_quaternion.to_euler('XYZ')
        elif armature_source_original.rotation_mode == 'AXIS_ANGLE':
            q = mathutils.Quaternion(
                armature_source_original.rotation_axis_angle[1:],
                armature_source_original.rotation_axis_angle[0])
            arm_rot_euler = q.to_euler('XYZ')
        else:
            arm_rot_euler = armature_source_original.rotation_euler.copy()

        arm_rot_mat = arm_rot_euler.to_matrix().to_4x4()
        arm_scale = armature_source_original.scale.copy()
        arm_scale_mat = mathutils.Matrix.Diagonal(arm_scale).to_4x4()
        arm_base_loc = armature_source_original.location.copy()

        # Source hip bone's rest pose info for space conversion
        if source_hip.parent:
            parent_rest_inv = source_hip.parent.bone.matrix_local.inverted()
            hip_rest_offset = (parent_rest_inv @ source_hip.bone.matrix_local).translation.copy()
        else:
            hip_rest_offset = source_hip.bone.matrix_local.translation.copy()

        # --- Step 3: Compute walking delta at each frame ---
        # Collect all unique frames from both bone and armature location fcurves
        all_frames = set()
        for fc_list in [hip_loc_fcurves, arm_loc_fcurves]:
            for fc in fc_list:
                if fc:
                    for kp in fc.keyframe_points:
                        all_frames.add(int(round(kp.co.x)))
        all_frames = sorted(all_frames)

        if not all_frames:
            print('RSL Root Motion: No frames found in source location data')
            return

        # Also read source hip ROTATION keyframes (needed for world position computation)
        source_hip_rot_path = f'pose.bones["{rm_source}"].rotation_quaternion'
        hip_rot_fcurves = {}
        for fcurve in source_action.fcurves:
            if fcurve.data_path == source_hip_rot_path:
                hip_rot_fcurves[fcurve.array_index] = fcurve

        if not hip_rot_fcurves:
            source_hip_rot_path = f'pose.bones["{rm_source}"].rotation_euler'
            for fcurve in source_action.fcurves:
                if fcurve.data_path == source_hip_rot_path:
                    hip_rot_fcurves[fcurve.array_index] = fcurve

        # Also read the armature's rotation keyframes (if any remain)
        arm_rot_fcurves = {}
        for fcurve in source_action.fcurves:
            if fcurve.data_path in ('rotation_euler', 'rotation_quaternion'):
                arm_rot_fcurves[fcurve.data_path + str(fcurve.array_index)] = fcurve

        # Compute the hip bone's world position at each frame
        hips_world_per_frame = {}
        for frame in all_frames:
            # Read hip bone location at this frame (in parent-local space)
            hip_loc = mathutils.Vector((0, 0, 0))
            for axis in range(3):
                fc = hip_loc_fcurves[axis]
                if fc:
                    hip_loc[axis] = fc.evaluate(frame)

            # Read armature object location at this frame (world space delta)
            arm_loc = mathutils.Vector((0, 0, 0))
            for axis in range(3):
                fc = arm_loc_fcurves[axis]
                if fc:
                    arm_loc[axis] = fc.evaluate(frame)

            # Compute armature's animated rotation at this frame
            arm_rot_frame = arm_rot_euler.copy()
            for key, fc in arm_rot_fcurves.items():
                if 'rotation_euler' in key:
                    idx = int(key[-1])
                    arm_rot_frame[idx] = fc.evaluate(frame)
            arm_rot_mat_frame = arm_rot_frame.to_matrix().to_4x4()

            # Compute the armature's animated world location
            arm_world_loc = arm_base_loc + arm_loc

            # Build the armature's world matrix at this frame
            arm_world_mat = mathutils.Matrix.Translation(arm_world_loc) @ arm_rot_mat_frame @ arm_scale_mat

            # Convert hip bone location to armature space
            if source_hip.parent:
                # Hip has a parent: location is in parent's local space
                # We need the parent's pose matrix at this frame.
                # For simplicity, we approximate: assume the parent's rotation
                # doesn't change the hip's position significantly for walking motion.
                # The walking delta is primarily in the hip's own location channel.
                hip_pos_armature = hip_rest_offset + hip_loc
            else:
                # Hip is parentless: location is in armature "channel space"
                hip_pos_armature = (source_hip.bone.matrix_local.to_3x3() @ hip_loc) + hip_rest_offset

            # Convert to world space
            hip_world = arm_world_mat @ mathutils.Vector(list(hip_pos_armature) + [1.0])
            hips_world_per_frame[frame] = hip_world.xyz.copy()

        # --- Step 4: Compute walking delta relative to first frame ---
        sorted_frames = sorted(hips_world_per_frame.keys())
        rest_frame = sorted_frames[0]
        hips_rest_world = hips_world_per_frame[rest_frame].copy()

        delta_per_frame = {}
        max_delta = 0.0
        for frame in sorted_frames:
            delta = hips_world_per_frame[frame] - hips_rest_world
            delta_per_frame[frame] = delta
            if delta.length > max_delta:
                max_delta = delta.length

        print(f'RSL Root Motion: Walking delta at last frame: {delta_per_frame[sorted_frames[-1]]} '
              f'(max={max_delta:.4f})')

        if max_delta < 0.001:
            print('RSL Root Motion: No significant walking motion detected in source animation')
            return

        # --- Step 5: Find the target ROOT bone and apply walking delta ---
        root_bone_name = ''
        root_bone = None
        for bone in armature_target.pose.bones:
            if not bone.parent:
                root_bone_name = bone.name
                root_bone = bone
                break

        if not root_bone:
            print('RSL Root Motion: No parentless root bone found in target armature')
            return

        # Get ROOT's rest pose rotation for channel-space conversion
        # For a parentless bone: visual = matrix_local @ Translation(location)
        # So: location = R_rest_inv @ delta_world
        root_rest_mat = root_bone.bone.matrix_local.copy()
        root_rest_rot = root_rest_mat.to_3x3()
        root_rest_rot_inv = root_rest_rot.inverted()

        print(f'RSL Root Motion: Applying {len(sorted_frames)} deltas to ROOT "{root_bone_name}"')

        # Switch to pose mode for keyframe insertion
        bpy.context.view_layer.objects.active = armature_target
        if armature_target.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='POSE')

        # Apply walking delta to ROOT bone
        for frame in sorted_frames:
            delta_world = delta_per_frame[frame]

            # Transform from world/armature space to ROOT's channel space
            delta_channel = root_rest_rot_inv @ delta_world

            root_bone.location = delta_channel
            root_bone.keyframe_insert(data_path='location', frame=frame)

        bpy.ops.object.mode_set(mode='OBJECT')

        # --- Step 6: Apply rest pose offset if needed ---
        if keep_offset and root_motion_rest_offsets:
            self.apply_root_motion_offset(armature_target, root_motion_rest_offsets)

        # --- Step 7: Verify ---
        root_loc_fcurves = []
        for fcurve in target_action.fcurves:
            if fcurve.data_path == f'pose.bones["{root_bone_name}"].location':
                root_loc_fcurves.append(fcurve)

        if len(root_loc_fcurves) >= 3:
            has_movement = False
            max_spread = 0.0
            for fc in root_loc_fcurves:
                values = [kp.co.y for kp in fc.keyframe_points]
                if len(values) > 1:
                    spread = abs(max(values) - min(values))
                    max_spread = max(max_spread, spread)
                    if spread > 0.001:
                        has_movement = True

            if has_movement:
                print(f'RSL Root Motion: SUCCESS - ROOT "{root_bone_name}" has '
                      f'location animation (max spread={max_spread:.4f})')
            else:
                print(f'RSL Root Motion: WARNING - ROOT "{root_bone_name}" has no '
                      f'significant movement (max spread={max_spread:.6f})')
        else:
            print(f'RSL Root Motion: WARNING - ROOT has only '
                  f'{len(root_loc_fcurves)} location fcurves (expected 3)')

    def apply_root_motion_offset(self, armature_target, root_motion_rest_offsets):
        """
        After baking and root motion extraction, adjust the location keyframes
        of the PARENTLESS ROOT bone so that the target character retains its own
        rest pose offset.

        After extract_root_motion_to_root_bone(), the walking motion is on the
        ROOT bone and HIPS location is zeroed (its fcurves may be removed).
        So we must apply the offset to the ROOT bone's location fcurves, not
        the HIPS bone's.

        The offset (in world space) must also be transformed to the ROOT bone's
        channel space, consistent with the channel-space correction in
        extract_root_motion_to_root_bone().
        """
        if not armature_target.animation_data or not armature_target.animation_data.action:
            return

        action = armature_target.animation_data.action

        # Find the parentless ROOT bone
        root_bone_name = ''
        root_rest_rot_inv = None
        for bone in armature_target.pose.bones:
            if not bone.parent:
                root_bone_name = bone.name
                # Get the inverse of ROOT's rest pose rotation for space conversion
                root_rest_rot_inv = bone.bone.matrix_local.to_3x3().inverted()
                break

        if not root_bone_name:
            print('RSL apply_root_motion_offset: No parentless root bone found')
            return

        # Compute the combined offset from all root motion bones
        # (typically there's only one: the hips bone)
        combined_offset_world = mathutils.Vector((0, 0, 0))
        for rm_target, offset_data in root_motion_rest_offsets.items():
            combined_offset_world += offset_data['offset']

        # Transform the offset from world space to ROOT's channel space
        # (same correction as in extract_root_motion_to_root_bone)
        offset_channel = root_rest_rot_inv @ combined_offset_world

        print(f'RSL apply_root_motion_offset: ROOT="{root_bone_name}", '
              f'offset_world={combined_offset_world}, offset_channel={offset_channel}')

        # Apply offset to the ROOT bone's location fcurves
        root_data_path = f'pose.bones["{root_bone_name}"].location'
        for fcurve in action.fcurves:
            if fcurve.data_path != root_data_path:
                continue

            axis = fcurve.array_index
            if axis < 3:
                for kp in fcurve.keyframe_points:
                    kp.co.y += offset_channel[axis]

        print(f'RSL apply_root_motion_offset: Applied offset to ROOT "{root_bone_name}" location fcurves')

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

    def compute_root_motion_from_source(self, armature_source_original, root_motion_bones,
                                         armature_target, frame_start, frame_end):
        """
        Compute root motion by sampling the SOURCE armature's hip bone world position
        at each frame via depsgraph evaluation.

        This method is MORE RELIABLE than reading baked HIPS location keyframes because:
        1. COPY_LOCATION WORLD/WORLD constraints often produce zero/missing location
           keyframes during NLA baking (a known Blender issue)
        2. We sample the ACTUAL animated position from the source, which we know has data
        3. No dependency on bake capturing constraint-driven location correctly

        Returns: dict mapping frame -> Vector (world-space walking delta from first frame)
                 or empty dict if no motion found.
        """
        if not root_motion_bones:
            return {}

        # Get the source hip bone name
        rm_source = list(root_motion_bones.values())[0]
        source_bone = armature_source_original.pose.bones.get(rm_source)
        if not source_bone:
            print(f'RSL compute_root_motion_from_source: Source bone "{rm_source}" not found')
            return {}

        print(f'RSL compute_root_motion_from_source: Sampling source bone "{rm_source}" '
              f'from frame {frame_start} to {frame_end}')

        # Sample the source bone's head position in world space at each frame
        hips_world_per_frame = {}
        for frame in range(frame_start, frame_end + 1):
            bpy.context.scene.frame_set(frame)
            dg = bpy.context.evaluated_depsgraph_get()
            # Get the source armature's evaluated object
            source_eval = armature_source_original.evaluated_get(dg)
            # Find the bone in the evaluated armature
            bone_eval = source_eval.pose.bones.get(rm_source)
            if bone_eval:
                # bone_eval.matrix is in armature-local space.
                # To get world space, we must apply the armature object's world matrix.
                hips_world_per_frame[frame] = (source_eval.matrix_world @ bone_eval.matrix).translation.copy()
            else:
                # Fallback: compute from matrix_world and bone head
                hips_world_per_frame[frame] = (source_eval.matrix_world @ source_bone.head).copy()

        if not hips_world_per_frame:
            print('RSL compute_root_motion_from_source: No frames sampled')
            return {}

        # Compute walking delta relative to first frame
        sorted_frames = sorted(hips_world_per_frame.keys())
        rest_pos = hips_world_per_frame[sorted_frames[0]]

        delta_per_frame = {}
        max_delta = 0.0
        for frame in sorted_frames:
            delta = hips_world_per_frame[frame] - rest_pos
            delta_per_frame[frame] = delta
            if delta.length > max_delta:
                max_delta = delta.length

        last_delta = delta_per_frame[sorted_frames[-1]]
        print(f'RSL compute_root_motion_from_source: Sampled {len(sorted_frames)} frames, '
              f'max delta={max_delta:.4f}, last delta={last_delta}')

        if max_delta < 0.001:
            print('RSL compute_root_motion_from_source: No significant walking motion detected')
            return {}

        return delta_per_frame

    def process_root_motion_after_bake(self, armature_target, root_motion_bones,
                                        root_bones, keep_offset, root_motion_rest_offsets,
                                        source_deltas=None):
        """
        Post-bake processing for root motion.

        UE5 extracts root motion from the PARENTLESS root bone in the skeleton.
        After retargeting, the walking motion is typically on the HIPS bone
        (which has COPY_LOCATION), while the parentless ROOT bone has little
        or no location animation.

        This method ALWAYS extracts motion from HIPS to ROOT. When source_deltas
        is provided (from compute_root_motion_from_source), it uses those directly
        instead of reading baked HIPS location keyframes (which are often zero).

        Math:
          delta_world = HIPS_current_world - HIPS_rest_world  (or from source_deltas)
          delta_channel = R_rest_inverse @ delta_world  (transform to ROOT channel space)
          ROOT.location = delta_channel
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

        # PRIORITY 1: Use source_deltas from compute_root_motion_from_source() if available.
        # This is the most reliable method because it samples the SOURCE armature's
        # actual animated position via depsgraph, bypassing the unreliable COPY_LOCATION
        # bake step entirely.
        #
        # PRIORITY 2: Fall back to extract_root_motion_to_root_bone() which reads
        # baked HIPS location keyframes. This may produce zero deltas if COPY_LOCATION
        # didn't bake correctly (common Blender issue with WORLD/WORLD constraints).
        used_source_deltas = False
        if source_deltas:
            used_source_deltas = self.apply_source_deltas_to_root(
                armature_target, root_bone_name, hips_bone_name, source_deltas)

        if not used_source_deltas:
            print('RSL Root Motion: Source deltas not available or empty, '
                  'falling back to baked HIPS location extraction')
            self.extract_root_motion_to_root_bone(armature_target, hips_bone_name, root_bone_name)

        # Apply rest pose offset
        if keep_offset and root_motion_rest_offsets:
            self.apply_root_motion_offset(armature_target, root_motion_rest_offsets)

    def apply_source_deltas_to_root(self, armature_target, root_bone_name, hips_bone_name, source_deltas):
        """
        Apply source-derived walking deltas directly to the ROOT bone.

        This is the PRIMARY and most reliable method for root motion transfer.
        It uses world-space deltas computed by compute_root_motion_from_source(),
        which samples the source armature's hip bone position via depsgraph.

        The deltas are in SOURCE armature world space. We need to convert them
        to the TARGET ROOT bone's channel space (same conversion as
        extract_root_motion_to_root_bone, but the input deltas come from
        the source directly, not from baked keyframes).

        Returns True if deltas were applied successfully, False otherwise.
        """
        if not source_deltas:
            return False

        if not armature_target.animation_data or not armature_target.animation_data.action:
            print('RSL apply_source_deltas_to_root: No animation data on target')
            return False

        action = armature_target.animation_data.action

        root_bone = armature_target.pose.bones.get(root_bone_name)
        hips_bone = armature_target.pose.bones.get(hips_bone_name)

        if not root_bone:
            print(f'RSL apply_source_deltas_to_root: ROOT bone "{root_bone_name}" not found')
            return False

        sorted_frames = sorted(source_deltas.keys())
        print(f'RSL apply_source_deltas_to_root: Applying {len(sorted_frames)} deltas to ROOT "{root_bone_name}"')

        # Get ROOT's rest pose rotation for channel-space conversion.
        # For a parentless bone: visual = matrix_local @ Translation(location)
        # So: location = R_rest_inv @ delta_world  (channel space)
        root_rest_mat = root_bone.bone.matrix_local.copy()
        root_rest_rot = root_rest_mat.to_3x3()
        root_rest_rot_inv = root_rest_rot.inverted()

        # Diagnostic: check if ROOT rest pose has significant rotation
        identity_3x3 = mathutils.Matrix.Identity(3)
        rot_diff = (root_rest_rot - identity_3x3).row[0].length + \
                   (root_rest_rot - identity_3x3).row[1].length + \
                   (root_rest_rot - identity_3x3).row[2].length
        if rot_diff > 0.01:
            print(f'  ROOT rest pose has rotation (deviation: {rot_diff:.4f}) - applying channel correction')
        else:
            print(f'  ROOT rest pose is near-identity - channel correction is minimal')

        # Switch to pose mode for keyframe insertion
        bpy.context.view_layer.objects.active = armature_target
        if armature_target.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='POSE')

        # Apply deltas to ROOT bone and zero out HIPS location
        for frame in sorted_frames:
            delta_world = source_deltas[frame]

            # Transform from world space to ROOT's channel space
            delta_channel = root_rest_rot_inv @ delta_world

            # Set ROOT location to carry the walking motion
            root_bone.location = delta_channel
            root_bone.keyframe_insert(data_path='location', frame=frame)

            # Zero out HIPS location (motion is now on ROOT)
            if hips_bone:
                hips_bone.location = (0, 0, 0)
                hips_bone.keyframe_insert(data_path='location', frame=frame)

        bpy.ops.object.mode_set(mode='OBJECT')

        # Remove HIPS location fcurves that are all zeros
        if hips_bone:
            fcurves_to_check = []
            for fcurve in action.fcurves:
                if fcurve.data_path == f'pose.bones["{hips_bone_name}"].location':
                    fcurves_to_check.append(fcurve)

            for fcurve in fcurves_to_check:
                all_zero = all(abs(kp.co.y) < 0.0001 for kp in fcurve.keyframe_points)
                if all_zero:
                    action.fcurves.remove(fcurve)

        # Verify ROOT location
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
                print(f'RSL apply_source_deltas_to_root: SUCCESS - ROOT "{root_bone_name}" has '
                      f'location animation (max delta={max_delta:.4f})')
            else:
                print(f'RSL apply_source_deltas_to_root: WARNING - ROOT "{root_bone_name}" has no '
                      f'significant movement (max delta={max_delta:.6f})')
                return False
        else:
            print(f'RSL apply_source_deltas_to_root: WARNING - ROOT has only '
                  f'{len(root_loc_fcurves)} location fcurves (expected 3)')
            return False

        return True

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

        Reads location keyframes directly from the baked action (not depsgraph)
        to avoid issues with constraints being active during evaluation.

        The HIPS bone's location is in its parent's local space. We convert it
        to armature/world space to compute the walking delta, then transform
        the delta to ROOT's channel space before setting ROOT.location.

        IMPORTANT: For a parentless bone, bone.location is NOT in armature space.
        The bone's world matrix is: matrix_local @ Translation(location) @ Rotation(rotation)
        So bone.location is in "channel space" that gets rotated by the rest pose
        rotation before being applied. We must transform the delta by the inverse
        of the rest pose rotation to get the correct channel-space value.
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

        # Read HIPS location keyframes from the action fcurves
        hips_data_path = f'pose.bones["{hips_bone_name}"].location'
        hips_loc_fcurves = [None, None, None]
        for fcurve in action.fcurves:
            if fcurve.data_path == hips_data_path and 0 <= fcurve.array_index <= 2:
                hips_loc_fcurves[fcurve.array_index] = fcurve

        if not any(hips_loc_fcurves):
            print(f'RSL Root Motion Extract: No location fcurves for HIPS "{hips_bone_name}"')
            return

        # Collect all keyframe frames and values
        hips_frame_data = {}  # frame -> [x, y, z]
        for axis in range(3):
            fc = hips_loc_fcurves[axis]
            if fc:
                for kp in fc.keyframe_points:
                    frame = int(round(kp.co.x))
                    if frame not in hips_frame_data:
                        hips_frame_data[frame] = [0.0, 0.0, 0.0]
                    hips_frame_data[frame][axis] = kp.co.y

        if not hips_frame_data:
            print('RSL Root Motion Extract: No HIPS location keyframes')
            return

        sorted_frames = sorted(hips_frame_data.keys())
        print(f'RSL Root Motion Extract: Extracting from "{hips_bone_name}" to '
              f'"{root_bone_name}" ({len(sorted_frames)} keyframes)')

        # We need to convert HIPS location from parent-local space to armature space.
        # Since the armature is at identity (after transform_apply), armature space = world space.
        #
        # For HIPS with parent ROOT:
        #   hips_world = root_rest_matrix @ (hips_rest_local_offset + hips_location)
        #   where hips_rest_local_offset is the HIPS rest head position in ROOT's local space
        #
        # Since ROOT hasn't moved yet (no animation), root_rest_matrix = root_bone.matrix (at rest)
        # But after transform_apply, the armature is at identity, so:
        #   root_world = root_bone.bone.matrix_local (rest pose in armature space)
        #   hips_world = root_world @ (hips_rest_offset + hips_loc)

        # Get the rest pose matrices
        # ROOT's rest matrix in armature space
        root_rest_mat = root_bone.bone.matrix_local.copy()

        # HIPS' rest offset in ROOT's local space
        if hips_bone.parent:
            parent_rest_inv = hips_bone.parent.bone.matrix_local.inverted()
            hips_rest_offset = (parent_rest_inv @ hips_bone.bone.matrix_local).translation.copy()
        else:
            hips_rest_offset = hips_bone.bone.matrix_local.translation.copy()

        # Compute HIPS world position at each frame
        # Note: We also need to account for ROOT's rotation affecting HIPS' position.
        # Since ROOT may have rotation keyframes from baking, we need to read those too.
        root_rot_mode = root_bone.rotation_mode
        if root_rot_mode == 'QUATERNION':
            root_rot_data_path = f'pose.bones["{root_bone_name}"].rotation_quaternion'
        else:
            root_rot_data_path = f'pose.bones["{root_bone_name}"].rotation_euler'

        # Read ROOT rotation keyframes
        root_rot_fcurves = {}
        for fcurve in action.fcurves:
            if fcurve.data_path == root_rot_data_path:
                root_rot_fcurves[fcurve.array_index] = fcurve

        # Also read ROOT location keyframes (if any, from baking)
        root_loc_data_path = f'pose.bones["{root_bone_name}"].location'
        root_loc_fcurves = [None, None, None]
        for fcurve in action.fcurves:
            if fcurve.data_path == root_loc_data_path and 0 <= fcurve.array_index <= 2:
                root_loc_fcurves[fcurve.array_index] = fcurve

        # Compute HIPS world position at each frame
        hips_world_per_frame = {}
        for frame in sorted_frames:
            # Get ROOT location at this frame (if any)
            root_loc = mathutils.Vector((0, 0, 0))
            for axis in range(3):
                fc = root_loc_fcurves[axis]
                if fc:
                    root_loc[axis] = fc.evaluate(frame)

            # Get ROOT rotation at this frame
            root_rot_mat = mathutils.Matrix.Identity(4)
            if root_rot_mode == 'QUATERNION':
                q = mathutils.Quaternion((1, 0, 0, 0))
                for idx, fc in root_rot_fcurves.items():
                    q[idx] = fc.evaluate(frame)
                root_rot_mat = q.to_matrix().to_4x4()
            else:
                e = mathutils.Euler((0, 0, 0), root_rot_mode)
                for idx, fc in root_rot_fcurves.items():
                    e[idx] = fc.evaluate(frame)
                root_rot_mat = e.to_matrix().to_4x4()

            # HIPS location in parent space
            hips_loc = mathutils.Vector(hips_frame_data.get(frame, [0, 0, 0]))

            # For the HIPS world position:
            # root_bone_world_matrix = root_rest_mat @ Translation(root_loc) @ root_rot_mat
            # hips_world = root_bone_world_matrix @ (hips_rest_offset + hips_loc)

            root_mat = root_rest_mat @ mathutils.Matrix.Translation(root_loc) @ root_rot_mat

            if hips_bone.parent:
                # HIPS position in parent (ROOT) space = rest_offset + location
                hips_pos_parent = hips_rest_offset + hips_loc
                # Convert to armature space
                hips_world = root_mat @ mathutils.Vector(list(hips_pos_parent) + [1.0])
                hips_world_per_frame[frame] = hips_world.xyz.copy()
            else:
                # HIPS is parentless, location is in armature space
                hips_world_per_frame[frame] = hips_rest_offset + hips_loc

        # Use first frame as rest reference
        rest_frame = sorted_frames[0]
        hips_rest_world = hips_world_per_frame[rest_frame].copy()

        # Compute delta for each frame
        print(f'  HIPS rest world (frame {rest_frame}): {hips_rest_world}')
        if len(sorted_frames) > 1:
            last_frame = sorted_frames[-1]
            delta_end = hips_world_per_frame[last_frame] - hips_rest_world
            print(f'  HIPS delta at frame {last_frame} (world space): {delta_end} (length={delta_end.length:.4f})')

        # CRITICAL FIX: Transform delta from world/armature space to ROOT's channel space.
        #
        # For a parentless bone, bone.location is NOT in armature space directly.
        # The bone's world matrix is: matrix_local @ Translation(location) @ Rotation(rotation)
        # So bone.location is in a "channel space" that gets rotated by the rest pose
        # rotation (matrix_local's 3x3 part) before being applied in armature space.
        #
        # The delta computed as (hips_world - hips_rest_world) is in armature/world space,
        # but bone.location needs it in channel space. We must undo the rest pose rotation:
        #
        #   correct_delta = R_rest_inverse @ delta_world
        #
        # Without this fix, when the armature had an object-level rotation (e.g. Mixamo's
        # 90° X rotation) that was applied via transform_apply, the ROOT bone's rest pose
        # matrix_local includes that rotation. The uncorrected delta gets rotated by R_rest
        # an extra time, causing:
        #   - Walking motion in the wrong direction (root bone appears stationary)
        #   - Character flipping upside down and moving downward (when R_rest is 90° X,
        #     forward motion (0,-1,0) becomes vertical motion (0,0,-1))
        root_rest_rot = root_rest_mat.to_3x3()
        root_rest_rot_inv = root_rest_rot.inverted()

        # Check if the rest pose has significant rotation (for diagnostic)
        identity_3x3 = mathutils.Matrix.Identity(3)
        rot_diff = (root_rest_rot - identity_3x3).row[0].length + \
                   (root_rest_rot - identity_3x3).row[1].length + \
                   (root_rest_rot - identity_3x3).row[2].length
        if rot_diff > 0.01:
            print(f'  ROOT rest pose has rotation (deviation from identity: {rot_diff:.4f})')
            print(f'  Applying channel-space correction to delta')
        else:
            print(f'  ROOT rest pose is near-identity, channel-space correction is minimal')

        # Switch to pose mode for keyframe insertion
        bpy.context.view_layer.objects.active = armature_target
        if armature_target.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='POSE')

        for frame in sorted_frames:
            delta_world = hips_world_per_frame[frame] - hips_rest_world

            # Transform from world/armature space to ROOT's channel space
            delta = root_rest_rot_inv @ delta_world

            # Set ROOT location to carry the walking motion
            root_bone.location = delta
            root_bone.keyframe_insert(data_path='location', frame=frame)

            # Zero out HIPS location
            hips_bone.location = (0, 0, 0)
            hips_bone.keyframe_insert(data_path='location', frame=frame)

        bpy.ops.object.mode_set(mode='OBJECT')

        # Remove HIPS location fcurves that are all zeros
        fcurves_to_check = []
        for fcurve in action.fcurves:
            if fcurve.data_path == f'pose.bones["{hips_bone_name}"].location':
                fcurves_to_check.append(fcurve)

        for fcurve in fcurves_to_check:
            all_zero = all(abs(kp.co.y) < 0.0001 for kp in fcurve.keyframe_points)
            if all_zero:
                action.fcurves.remove(fcurve)

        # Verify ROOT location
        root_loc_fcurves_new = []
        for fcurve in action.fcurves:
            if fcurve.data_path == f'pose.bones["{root_bone_name}"].location':
                root_loc_fcurves_new.append(fcurve)

        if len(root_loc_fcurves_new) >= 3:
            has_movement = False
            max_delta = 0.0
            for fc in root_loc_fcurves_new:
                values = [kp.co.y for kp in fc.keyframe_points]
                if len(values) > 1:
                    spread = abs(max(values) - min(values))
                    max_delta = max(max_delta, spread)
                    if spread > 0.001:
                        has_movement = True

            if has_movement:
                print(f'RSL Root Motion Extract: SUCCESS - ROOT "{root_bone_name}" has '
                      f'location animation (max delta={max_delta:.4f})')
            else:
                print(f'RSL Root Motion Extract: WARNING - ROOT "{root_bone_name}" has no '
                      f'significant movement (max delta={max_delta:.6f})')
        else:
            print(f'RSL Root Motion Extract: WARNING - ROOT has only '
                  f'{len(root_loc_fcurves_new)} location fcurves (expected 3)')

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
