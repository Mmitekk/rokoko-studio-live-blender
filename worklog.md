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
---
Task ID: 1
Agent: Main Agent
Task: Fix updater version 999.0.0 + Fix root motion pipeline

Work Log:
- Analyzed user screenshot showing "999.0.0" version in updater dropdown
- Found root cause: _add_master_branch_fallback() uses hardcoded tag_name '999.0.0'
- Added _github_fetch_text() method for raw.githubusercontent.com (returns text, not JSON)
- Updated _add_master_branch_fallback() to fetch actual version from remote __init__.py
- Version now shows as "v2.4.1 (master abc1234)" instead of "999.0.0"
- Analyzed entire retargeting.py (~3400 lines) to understand root motion pipeline
- Identified key issue: precompile_root_motion() uses depsgraph which may not evaluate correctly
- Added compile_root_motion_from_fcurves() method that reads source hip fcurves DIRECTLY
- Moved root motion compilation to AFTER transfer_armature_location_to_hip() for reliability
- Kept depsgraph as fallback if direct fcurve reading fails
- Bumped version to 2.4.1
- Pushed to GitHub

Stage Summary:
- Updater now shows real version number instead of 999.0.0
- Root motion pipeline redesigned with direct fcurve reading as primary method
- Version bumped to 2.4.1
- All changes pushed to GitHub master branch
