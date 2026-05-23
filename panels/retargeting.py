import bpy

from .main import ToolPanel
from ..operators import retargeting, detector
from ..core.icon_manager import Icons
from ..core.retargeting import get_target_armature, get_source_armature

from bpy.types import PropertyGroup, UIList
from bpy.props import StringProperty, BoolProperty


# Retargeting panel
class RetargetingPanel(ToolPanel, bpy.types.Panel):
    bl_idname = 'VIEW3D_PT_rsl_retargeting_v2'
    bl_label = 'Retargeting'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False

        row = layout.row(align=True)
        row.label(text='Select the armatures:')

        row = layout.row(align=True)
        row.prop(context.scene, 'rsl_retargeting_armature_source', icon='ARMATURE_DATA')

        row = layout.row(align=True)
        row.prop(context.scene, 'rsl_retargeting_armature_target', icon='ARMATURE_DATA')

        anim_exists = False
        for obj in bpy.data.objects:
            if obj.animation_data and obj.animation_data.action:
                anim_exists = True

        if not anim_exists:
            row = layout.row(align=True)
            row.label(text='No animated armature found!', icon='INFO')
            return

        if not context.scene.rsl_retargeting_armature_source or not context.scene.rsl_retargeting_armature_target:
            self.draw_import_export(layout)
            return

        if not context.scene.rsl_retargeting_bone_list:
            row = layout.row(align=True)
            row.scale_y = 1.2
            row.operator(retargeting.BuildBoneList.bl_idname, icon_value=Icons.CALIBRATE.get_icon())
            self.draw_import_export(layout)
            return

        subrow = layout.row(align=True)
        row = subrow.row(align=True)
        row.scale_y = 1.2
        row.operator(retargeting.BuildBoneList.bl_idname, text='Rebuild Bone List', icon_value=Icons.CALIBRATE.get_icon())
        row = subrow.row(align=True)
        row.scale_y = 1.2
        row.alignment = 'RIGHT'
        row.operator(retargeting.ClearBoneList.bl_idname, text="", icon='X')

        layout.separator()

        row = layout.row(align=True)
        row.template_list("RSL_UL_BoneList", "Bone List", context.scene, "rsl_retargeting_bone_list", context.scene, "rsl_retargeting_bone_list_index", rows=1, maxrows=10)

        row = layout.row(align=True)
        row.operator(retargeting.AddBoneListItem.bl_idname, text="Add Custom Entry", icon='ADD')

        row = layout.row(align=True)
        row.prop(context.scene, 'rsl_retargeting_auto_scaling')

        row = layout.row(align=True)
        row.label(text='Use Pose:')
        row.prop(context.scene, 'rsl_retargeting_use_pose', expand=True)

        layout.separator()

        # Root Motion settings
        row = layout.row(align=True)
        row.label(text='Root Motion:')
        row.prop(context.scene, 'rsl_retargeting_root_motion_mode', expand=True)

        root_motion_mode = context.scene.rsl_retargeting_root_motion_mode
        armature_source = get_source_armature()
        armature_target = get_target_armature()

        if root_motion_mode == 'CUSTOM':
            if armature_source:
                row = layout.row(align=True)
                row.prop_search(context.scene, 'rsl_retargeting_root_bone_source',
                                armature_source.pose, 'bones', icon='BONE_DATA')
            if armature_target:
                row = layout.row(align=True)
                row.prop_search(context.scene, 'rsl_retargeting_root_bone_target',
                                armature_target.pose, 'bones', icon='BONE_DATA')
            row = layout.row(align=True)
            row.prop(context.scene, 'rsl_retargeting_root_motion_keep_offset')
        elif root_motion_mode == 'AUTO':
            row = layout.row(align=True)
            row.prop(context.scene, 'rsl_retargeting_root_motion_keep_offset')
            row = layout.row(align=True)
            row.label(text='  (Auto-detecting hip bone)', icon='INFO')

        layout.separator()

        # T-Pose Reference settings
        row = layout.row(align=True)
        row.label(text='T-Pose Reference:', icon='ARMATURE_DATA')

        row = layout.row(align=True)
        row.prop(context.scene, 'rsl_retargeting_tpose_reference', icon='ARMATURE_DATA')

        tpose_ref = context.scene.rsl_retargeting_tpose_reference
        if tpose_ref:
            row = layout.row(align=True)
            row.prop(context.scene, 'rsl_retargeting_tpose_apply_before')

            row = layout.row(align=True)
            row.scale_y = 1.1
            row.operator(retargeting.ApplyTPoseReference.bl_idname, icon='CON_ROTLIKE')

            # Show bone match info
            armature_src = get_source_armature()
            if armature_src:
                matched = sum(1 for b in tpose_ref.pose.bones if b.name in armature_src.pose.bones)
                total = len(tpose_ref.pose.bones)
                row = layout.row(align=True)
                row.label(text=f'  Matching bones: {matched}/{total}', icon='GROUP_BONE')
        else:
            row = layout.row(align=True)
            row.label(text='  (Select T-pose armature to fix A-pose issues)', icon='INFO')

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(retargeting.RetargetAnimation.bl_idname, icon_value=Icons.CALIBRATE.get_icon())

        # UE5 Export section
        layout.separator()
        row = layout.row(align=True)
        row.label(text='UE5 Export:', icon='EXPORT')

        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator(retargeting.ExportFBXForUE5.bl_idname, icon='EXPORT')

        row = layout.row(align=True)
        row.scale_y = 1.0
        row.operator(retargeting.PrepareForUE5.bl_idname, text='Prep Only (Manual FBX)', icon='SETTINGS')

        row = layout.row(align=True)
        row.scale_y = 0.7
        row.label(text='  Use "Export FBX for UE5" for best results', icon='INFO')

        self.draw_import_export(layout)

    def draw_import_export(self, layout):
        layout.separator()

        row = layout.row(align=True)
        row.label(text='Custom Naming Schemes:')

        subrow = layout.row(align=True)
        row = subrow.row(align=True)
        row.scale_y = 0.9
        row.operator(detector.SaveCustomBonesRetargeting.bl_idname, text='Save')
        row.operator(detector.ImportCustomBones.bl_idname, text='Import')
        row.operator(detector.ExportCustomBones.bl_idname, text='Export')
        row = subrow.row(align=True)
        row.scale_y = 0.9
        row.alignment = 'RIGHT'
        row.operator(detector.ClearCustomBones.bl_idname, text='', icon='X')


class BoneListItem(PropertyGroup):
    """Properties of the bone list items"""
    bone_name_source: StringProperty(
        name="Source Bone",
        description="The source bone name",
        default="")

    bone_name_target: StringProperty(
        name="Target Bone",
        description="The target bone name",
        default="")

    bone_name_key: StringProperty(
        name="Auto Detection Key",
        description="The automatically detected bone key",
        default="")

    is_custom: BoolProperty(
        description="This determines if the field is a custom one source bone one",
        default=False)


class RSL_UL_BoneList(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        armature_target = get_target_armature()
        armature_source = get_source_armature()

        layout = layout.split(factor=0.36, align=True)

        # Displays source bone
        if item.is_custom:
            layout.prop_search(item, 'bone_name_source', armature_source.pose, "bones", text='')
        else:
            layout.label(text=item.bone_name_source)

        # Displays target bone
        if armature_target:
            layout.prop_search(item, 'bone_name_target', armature_target.pose, "bones", text='')
