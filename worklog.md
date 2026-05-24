---
Task ID: 1
Agent: Main Agent
Task: Fix root motion retargeting in Rokoko Studio Live Blender plugin

Work Log:
- Cloned the forked repository from https://github.com/Mmitekk/rokoko-studio-live-blender
- Analyzed uploaded screenshots showing: (1) armature vertically flipped with head pointing down, (2) character walking forward but with root motion issues
- Deep analysis of the entire retargeting codebase: operators/retargeting.py (2115 lines), core/animations.py, core/recorder.py, core/state_manager.py, properties.py, panels/retargeting.py
- Identified 3 critical bugs in root motion handling:
  1. extract_root_motion_to_root_bone reads positions via depsgraph while constraints are still active (causes double-application)
  2. Rotation restoration after retargeting causes double-baking (R² instead of R) - THIS is the Force Root Lock flip bug
  3. transfer_armature_location_to_hip has wrong space conversion (applies rotation instead of inverse, multiplies by scale instead of dividing)
- Implemented fixes for all 3 bugs
- Verified Python syntax passes
- Committed as v1.8.0 and pushed to GitHub

Stage Summary:
- Fixed 3 critical root motion bugs in operators/retargeting.py
- Version bumped from 1.7.2 to 1.8.0 in __init__.py
- Changes pushed to https://github.com/Mmitekk/rokoko-studio-live-blender
