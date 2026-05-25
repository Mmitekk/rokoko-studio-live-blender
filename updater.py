import os
import bpy
import ssl
import time
import json
import urllib
import shutil
import pathlib
import zipfile
import traceback
import addon_utils
from threading import Thread
from bpy.app.handlers import persistent

GITHUB_REPO = "Mmitekk/rokoko-studio-live-blender"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}"
GITHUB_URL = f"{GITHUB_API_URL}/releases"
GITHUB_URL_MASTER = f"https://github.com/{GITHUB_REPO}/archive/master.zip"
GITHUB_URL_ZIPBALL = f"{GITHUB_API_URL}/zipball/master"
GITHUB_URL_COMMITS = f"{GITHUB_API_URL}/commits/master"
GITHUB_COMPATIBILITY_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/master/version_compatibility.json"

downloads_dir_name = "updater_downloads"

path_names_to_keep = [
    downloads_dir_name,
    'resources/no_auto_ver_check.txt',
    'resources/cache',
    'resources/custom_bones',
]

# Dev testing variables
no_ver_check = False
fake_update = False

# Updater variables
version_list = []
is_checking_for_update = False
checked_on_startup = False
current_version = []
current_version_str = ''
update_needed = False
latest_version = None
latest_version_str = ''
used_updater_panel = False
update_finished = False
remind_me_later = False
is_ignored_version = False

confirm_update_to = ''

show_error = ''

file_replacement_extension = '.renamed'

# GitHub token for authenticated API requests (higher rate limit: 5000/hour vs 60/hour)
# Can be set via addon preferences
github_token = ''

main_dir = os.path.dirname(__file__)
downloads_dir = os.path.join(main_dir, downloads_dir_name)
resources_dir = os.path.join(main_dir, "resources")
ignore_ver_file = os.path.join(resources_dir, "ignore_version.txt")
no_auto_ver_check_file = os.path.join(resources_dir, "no_auto_ver_check.txt")
delete_files_on_startup_file = os.path.join(main_dir, "delete_files_on_startup.txt")
compatibility_file = os.path.join(main_dir, "version_compatibility.json")

# Compatibility checking variables
compatibility_data = {}
compatibility_loaded = False

# Get package name, important for panel in user preferences
package_name = ''
for mod in addon_utils.modules():
    if mod.bl_info['name'] == 'Rokoko Studio Live for Blender':
        package_name = mod.__name__


def _get_github_token():
    """Get the GitHub token from the addon preferences if available."""
    global github_token
    if github_token:
        return github_token
    # Try to read from addon preferences
    try:
        prefs = get_user_preferences()
        addon_prefs = prefs.addons.get(package_name)
        if addon_prefs and hasattr(addon_prefs.preferences, 'github_token'):
            token = addon_prefs.preferences.github_token
            if token:
                github_token = token
                return token
    except Exception:
        pass
    return github_token


def _github_api_request(url, timeout=30):
    """Make a GitHub API request with optional authentication.

    Uses the github_token if available for higher rate limits.
    Returns the parsed JSON data, or raises an exception on failure.
    """
    ssl._create_default_https_context = ssl._create_unverified_context

    request = urllib.request.Request(url)
    request.add_header('User-Agent', 'Rokoko-Studio-Live-Blender-Updater')

    token = _get_github_token()
    if token:
        request.add_header('Authorization', f'token {token}')

    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def _github_download_file(url, dest_path):
    """Download a file from GitHub with optional authentication.

    Args:
        url: The URL to download from
        dest_path: The destination file path

    Returns True on success, False on failure.
    """
    ssl._create_default_https_context = ssl._create_unverified_context

    token = _get_github_token()
    if token:
        # Use authenticated download via urllib Request with auth header
        request = urllib.request.Request(url)
        request.add_header('User-Agent', 'Rokoko-Studio-Live-Blender-Updater')
        request.add_header('Authorization', f'token {token}')

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                with open(dest_path, 'wb') as out_file:
                    # Download in chunks to handle large files
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        out_file.write(chunk)
            return True
        except urllib.error.URLError as e:
            print(f'RSL Updater: Authenticated download failed: {e}')
            # Fall back to unauthenticated download
            pass

    # Unauthenticated download (original method)
    try:
        urllib.request.urlretrieve(url, dest_path)
        return True
    except urllib.error.URLError as e:
        print(f'RSL Updater: Unauthenticated download failed: {e}')
        return False


class Version:
    def __init__(self, data):
        # Set version string
        version_string = data.get('tag_name').lower().replace('-', '.').replace('_', '.')
        if version_string.startswith('v.'):
            version_string = version_string[2:]
        if version_string.startswith('v'):
            version_string = version_string[1:]

        # Set version number
        version_number = []
        for i in version_string.split('.'):
            if i.isdigit():
                version_number.append(int(i))

        # Set version data
        self.version_string = version_string
        self.version_display_string = version_string
        self.version_number = version_number
        self.name = data.get('name')
        self.download_link = data.get('zipball_url')
        self.patch_notes = data.get('body')
        self.release_date = data.get('published_at')
        self.is_prerelease = data.get('prerelease')

        if 'T' in data.get('published_at')[1:]:
            self.release_date = data.get('published_at').split('T')[0]

        # If the name of the release contains "yanked", ignore it
        if 'yanked' in self.name.lower():
            return

        if self.is_prerelease:
            self.version_display_string += ' (beta)'

        version_list.append(self)


def get_version_by_string(version_string) -> Version:
    for version in version_list:
        if version.version_string == version_string:
            return version


def get_latest_version() -> Version:
    version_list_releases = [version for version in version_list if not version.is_prerelease and is_version_compatible(version.version_string)]
    return version_list_releases[0] if version_list_releases else None


def load_compatibility_data():
    """Load the version compatibility JSON from GitHub."""
    global compatibility_data, compatibility_loaded

    if compatibility_loaded:
        return True

    try:
        print("Fetching version compatibility data from GitHub...")
        data = _github_api_request(GITHUB_COMPATIBILITY_URL)
        # raw.githubusercontent.com returns the file content directly, not JSON array/dict
        if isinstance(data, (dict, list)):
            compatibility_data = data
        else:
            compatibility_data = json.loads(data) if isinstance(data, str) else {}
        compatibility_loaded = True
        print("Loaded version compatibility data from GitHub")
        return True
    except urllib.error.URLError as e:
        print(f"Failed to fetch compatibility data from GitHub: {e}")
        # Try to load from local file as fallback
        try:
            if os.path.isfile(compatibility_file):
                with open(compatibility_file, 'r', encoding='utf-8') as f:
                    compatibility_data = json.load(f)
                compatibility_loaded = True
                print("Loaded version compatibility data from local file as fallback")
                return True
        except Exception as local_e:
            print(f"Failed to load local compatibility file: \n{traceback.format_exc()}")

        print("No compatibility data available, all versions will be considered compatible")
        compatibility_data = {}
        compatibility_loaded = True
        return False
    except Exception as e:
        print(f"Error loading compatibility data: \n{traceback.format_exc()}")
        compatibility_data = {}
        compatibility_loaded = True
        return False


def get_compatibility_for_version(addon_version_string):
    """Get compatibility info for a specific addon version.

    Since the JSON only contains versions where compatibility changed,
    we need to find the highest version <= the requested version.
    """
    if not compatibility_data:
        return None

    # Convert version string to tuple for comparison
    def version_to_tuple(version_str):
        try:
            return tuple(int(x) for x in version_str.split('.'))
        except:
            return (0, 0, 0)

    addon_version_tuple = version_to_tuple(addon_version_string)

    # Find the highest version in compatibility data that is <= addon_version
    best_match = None
    best_match_tuple = (0, 0, 0)

    for compat_version in compatibility_data.keys():
        compat_tuple = version_to_tuple(compat_version)
        if compat_tuple <= addon_version_tuple and compat_tuple > best_match_tuple:
            best_match = compat_version
            best_match_tuple = compat_tuple

    if best_match:
        return compatibility_data[best_match]

    return None


def refresh_compatibility_data():
    """Force refresh of compatibility data from GitHub."""
    global compatibility_loaded
    compatibility_loaded = False
    return load_compatibility_data()


def is_version_compatible(addon_version_string):
    """Check if an addon version is compatible with the current Blender version."""
    # Load compatibility data if not already loaded
    if not load_compatibility_data():
        # If no compatibility file, assume all versions are compatible
        return True

    # Get current Blender version as string
    blender_version = ".".join(str(x) for x in bpy.app.version)
    blender_version_tuple = bpy.app.version

    # Get compatibility info for this addon version
    compat_info = get_compatibility_for_version(addon_version_string)
    if not compat_info:
        # No compatibility info found, assume compatible
        return True

    # Check minimum version
    min_blender = compat_info.get('minimum_blender')
    if min_blender:
        try:
            min_tuple = tuple(int(x) for x in min_blender.split('.'))
            if blender_version_tuple < min_tuple:
                return False
        except:
            pass

    # Check maximum version
    max_blender = compat_info.get('maximum_blender')
    if max_blender:
        try:
            max_tuple = tuple(int(x) for x in max_blender.split('.'))
            if blender_version_tuple > max_tuple:
                return False
        except:
            pass

    return True


def check_for_update_background(check_on_startup=False):
    global is_checking_for_update, checked_on_startup
    if check_on_startup and checked_on_startup:
        # print('ALREADY CHECKED ON STARTUP')
        return
    if is_checking_for_update:
        # print('ALREADY CHECKING')
        return

    checked_on_startup = True

    if check_on_startup and os.path.isfile(no_auto_ver_check_file):
        print('AUTO CHECK DISABLED VIA FILE')
        return

    is_checking_for_update = True

    thread = Thread(target=check_for_update, args=[])
    thread.start()


def check_for_update():
    print('Checking for Rokoko Studio Live update...')

    # Refresh compatibility data from GitHub
    global compatibility_loaded
    compatibility_loaded = False  # Force reload
    load_compatibility_data()

    # Get all releases from Github
    if not get_github_releases():
        finish_update_checking(error='Could not check for updates,'
                                     '\ntry again later.')
        return

    if not version_list:
        finish_update_checking(error='No plugin versions available.')
        return

    # Check if an update is needed
    global update_needed, is_ignored_version
    update_needed = check_for_update_available()
    is_ignored_version = check_ignored_version()

    # Update needed, show the notification popup if it wasn't checked through the UI
    if update_needed:
        print('Update found!')
        if not used_updater_panel and not is_ignored_version:
            prepare_to_show_update_notification()
    else:
        print('No update found.')

    # Finish update checking, update the UI
    finish_update_checking()


def get_github_releases():
    global version_list
    version_list = []

    if fake_update:
        print('FAKE INSTALL!')

        Version({
            'tag_name': '100.1',
            'name': 'Pre release!',
            'zipball_url': '',
            'body': 'Nothing new to see',
            'published_at': 'Just now!!',
            'prerelease': True
        })

        Version({
            'tag_name': 'v-99-99',
            'name': 'v-99-99',
            'zipball_url': '',
            'body': 'Put exiting new stuff here\nOr maybe there is?',
            'published_at': 'Today',
            'prerelease': False
        })

        Version({
            'tag_name': '12.34.56',
            'name': '12.34.56 Test Release',
            'zipball_url': '',
            'body': 'Nothing new to see',
            'published_at': 'A week ago probably',
            'prerelease': False
        })
        return True

    try:
        data = _github_api_request(GITHUB_URL)
    except urllib.error.URLError as e:
        print(f'RSL Updater: GitHub releases API error: {e}')
        # Fall back to master branch - don't just fail completely
        _add_master_branch_fallback()
        return True
    except Exception as e:
        print(f'RSL Updater: Unexpected error fetching releases: {e}')
        _add_master_branch_fallback()
        return True

    if not data:
        if type(data) == list:
            # No releases found — offer master branch as a fallback version
            _add_master_branch_fallback()
            return True
        return False

    for version_data in data:
        Version(version_data)

    # If no releases were parsed (all yanked etc.), offer master branch
    if not version_list:
        _add_master_branch_fallback()

    return True


def _add_master_branch_fallback():
    """Add a virtual 'Latest from master' version when no GitHub releases exist."""
    global version_list
    print('RSL Updater: Offering master branch as available version...')

    commit_sha = 'unknown'
    commit_date = 'Unknown'
    try:
        commit_data = _github_api_request(GITHUB_URL_COMMITS)
        commit_sha = commit_data.get('sha', 'unknown')[:7]
        commit_date = commit_data.get('commit', {}).get('committer', {}).get('date', 'Unknown')
    except Exception as e:
        print(f'RSL Updater: Could not fetch commit info: {e}')

    # Use API zipball URL which works with authentication
    download_url = GITHUB_URL_ZIPBALL
    # If no token is available, fall back to the regular GitHub archive URL
    if not _get_github_token():
        download_url = GITHUB_URL_MASTER

    Version({
        'tag_name': '999.0.0',
        'name': f'Latest master ({commit_sha})',
        'zipball_url': download_url,
        'body': f'Latest version from the master branch.\nCommit: {commit_sha}\nDate: {commit_date}',
        'published_at': commit_date if commit_date != 'Unknown' else '2025-01-01T00:00:00Z',
        'prerelease': False  # Not marked as prerelease so it shows up as an available update
    })


def check_for_update_available() -> bool:
    if not version_list:
        return False

    global latest_version, latest_version_str
    latest_compatible_version = get_latest_version()

    # No compatible versions found
    if not latest_compatible_version:
        return False

    latest_version = latest_compatible_version.version_number
    latest_version_str = latest_compatible_version.version_string

    if latest_version > current_version:
        return True
    return False


def finish_update_checking(error=''):
    global is_checking_for_update, show_error
    is_checking_for_update = False

    # Only show error if the update panel was used before
    if used_updater_panel:
        show_error = error

    ui_refresh()


def ui_refresh():
    # A way to refresh the ui
    refreshed = False
    while not refreshed:
        if hasattr(bpy.data, 'window_managers'):
            for windowManager in bpy.data.window_managers:
                for window in windowManager.windows:
                    for area in window.screen.areas:
                        area.tag_redraw()
            refreshed = True
            # print('Refreshed UI')
        else:
            time.sleep(0.5)


def get_update_post():
    if hasattr(bpy.app.handlers, 'scene_update_post'):
        return bpy.app.handlers.scene_update_post
    else:
        return bpy.app.handlers.depsgraph_update_post


def prepare_to_show_update_notification():
    return  # TODO: Implement?
    # This is necessary to show a popup directly after startup
    # You will get a nasty error otherwise
    # This will add the function to the scene_update_post and it will be executed every frame. that's why it needs to be removed again asap
    # print('PREPARE TO SHOW UI')
    if show_update_notification not in get_update_post():
        get_update_post().append(show_update_notification)


@persistent
def show_update_notification(scene):  # One argument in necessary for some reason
    # print('SHOWING UI NOW!!!!')

    # # Immediately remove this from handlers again
    if show_update_notification in get_update_post():
        get_update_post().remove(show_update_notification)

    # Show notification popup
    # atr = UpdateNotificationPopup.bl_idname.split(".")
    # getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')
    bpy.ops.rsl_updater.update_notification_popup('INVOKE_DEFAULT')


def update_now(version=None, latest=False, beta=False):
    if fake_update:
        print('FAKE UPDATE TO VERSION:', version)
        finish_update()
        return
    if beta:
        print('UPDATE TO BETA / MASTER')
        # Use API zipball URL which works with authentication
        if _get_github_token():
            update_link = GITHUB_URL_ZIPBALL
        else:
            update_link = GITHUB_URL_MASTER
    elif latest or not version:
        print('UPDATE TO ' + latest_version_str)
        update_link = get_latest_version().download_link
        bpy.context.scene.rsl_updater_version_list = latest_version_str
    else:
        print('UPDATE TO ' + version)
        update_link = get_version_by_string(version).download_link

    download_file(update_link)


def download_file(update_url):
    if not update_url:
        finish_update()
        return

    # Load all the directories and files
    update_zip_file = os.path.join(downloads_dir, "rokoko-update.zip")

    # Remove existing download folder
    if os.path.isdir(downloads_dir):
        print("DOWNLOAD FOLDER EXISTED")
        shutil.rmtree(downloads_dir)

    # Create download folder
    pathlib.Path(downloads_dir).mkdir(exist_ok=True)

    # Download zip
    print('DOWNLOAD FILE from:', update_url)
    if not _github_download_file(update_url, update_zip_file):
        print("FILE COULD NOT BE DOWNLOADED")
        shutil.rmtree(downloads_dir)
        finish_update(error='Could not download update.'
                            '\nCheck your internet connection or'
                            '\nadd a GitHub token in preferences.')
        return
    print('DOWNLOAD FINISHED')

    # If zip is not downloaded, abort
    if not os.path.isfile(update_zip_file):
        print("ZIP NOT FOUND!")
        shutil.rmtree(downloads_dir)
        finish_update(error='Could not find the'
                            '\ndownloaded zip.')
        return

    # Extract the downloaded zip
    print('EXTRACTING ZIP')
    with zipfile.ZipFile(update_zip_file, "r") as zip_ref:
        zip_ref.extractall(downloads_dir)
    print('EXTRACTED')

    # Delete the extracted zip file
    print('REMOVING ZIP FILE')
    os.remove(update_zip_file)

    # Detect the extracted folders and files
    print('SEARCHING FOR INIT 1')

    def search_init(path):
        print('SEARCHING IN ' + path)
        files = os.listdir(path)
        if "__init__.py" in files:
            print('FOUND')
            return path
        folders = [f for f in os.listdir(path) if os.path.isdir(os.path.join(path, f))]
        if len(folders) != 1:
            print(len(folders), 'FOLDERS DETECTED')
            return None
        print('GOING DEEPER')
        return search_init(os.path.join(path, folders[0]))

    print('SEARCHING FOR INIT 2')
    extracted_zip_dir = search_init(downloads_dir)
    if not extracted_zip_dir:
        print("INIT NOT FOUND!")
        shutil.rmtree(downloads_dir)
        finish_update(error='Could not find Rokoko Studio'
                            '\nLive in the downloaded zip.')
        return

    # Remove old addon files
    clean_addon_dir()

    # Move the extracted files to their correct places
    def move_files(from_dir, to_dir):
        print('MOVE FILES TO DIR:', to_dir)
        files = os.listdir(from_dir)
        for file in files:
            source_path = os.path.join(from_dir, file)
            target_path = os.path.join(to_dir, file)
            print('MOVE', source_path)

            # If file exists, delete the target and move the new file over
            if os.path.isfile(source_path) and os.path.isfile(target_path):
                try:
                    os.remove(target_path)
                except PermissionError as e:
                    # If removing the target file failed, rename the new file, add its name to a file and move it over
                    # It will re renamed on the next Blender startup
                    print(e)
                    source_path_renamed = os.path.join(from_dir, file) + file_replacement_extension
                    os.rename(source_path, source_path_renamed)
                    source_path = source_path_renamed
                    print('File was not deleted, it will be replaced on the next startup')

                try:
                    shutil.move(source_path, to_dir)
                except shutil.Error as e:
                    print('Moving still failed:', e)

                print('REMOVED AND MOVED', file)

            elif os.path.isdir(source_path) and os.path.isdir(target_path):
                move_files(source_path, target_path)

            else:
                try:
                    shutil.move(source_path, to_dir)
                except shutil.Error as e:
                    print(e)
                print('MOVED', file)

    move_files(extracted_zip_dir, main_dir)

    # Delete download folder
    print('DELETE DOWNLOADS DIR')
    shutil.rmtree(downloads_dir)

    # Finish the update
    finish_update()


def finish_update(error=''):
    global update_finished, show_error
    show_error = error

    if not error:
        update_finished = True

    bpy.ops.rsl_updater.update_complete_panel('INVOKE_DEFAULT')
    ui_refresh()
    print("UPDATE DONE!")


def clean_addon_dir():
    print("CLEAN ADDON FOLDER")

    # Convert paths to os specific paths
    paths_to_keep = []
    for path_name in path_names_to_keep:
        path_parts = path_name.split('/')
        paths_to_keep.append(os.path.join(*path_parts))

    for root, dirs, files in os.walk(main_dir, topdown=False):
        root_rel = os.path.relpath(root, main_dir)

        # Ignore folders that start with a dot. If the relative path is a dot only, it means that it's the main path which shouldn't be ignored
        if root_rel.startswith('.') and root_rel != '.':
            continue

        # Go over every file and decide whether to delete it or not
        for file in files:
            file_rel = os.path.join(root_rel, file)
            file_abs = os.path.join(root, file)

            if file_rel.startswith('.\\') or file_rel.startswith('./'):
                file_rel = file_rel[2:]

            # Keep the file if its exact name is on the ignore list
            if file_rel in paths_to_keep:
                continue

            # Keep the file if part of its path is on the ignore list
            keep_file = False
            for path in paths_to_keep:
                if file_rel.startswith(path):
                    keep_file = True
                    break
            if keep_file:
                continue

            # Delete the file
            try:
                os.remove(file_abs)
                print('Removed file', file_abs)
            except OSError:
                print('Failed to remove file', file_abs)
                add_file_to_delete_on_startup(file_abs)

        # Go over every folder and decide whether to delete it or not
        for folder in dirs:
            folder_rel = os.path.join(root_rel, folder)
            folder_abs = os.path.join(root, folder)
            if folder_rel.startswith('.\\'):
                folder_rel = folder_rel[2:]

            # Keep the folder if its exact name is on the ignore list
            if folder_rel in paths_to_keep:
                continue

            # Delete the folder. It won't get deleted if it's not empty and that is on purpose.
            # All files in the folder should be deleted already, so keep it if there are still files in it
            try:
                os.rmdir(folder_abs)
                print('Removed folder', folder_abs)
            except OSError:
                print('Failed to remove folder', folder_abs)


def add_file_to_delete_on_startup(file_path):
    # w = create and write
    # a = append to end of file
    write_type = 'a' if os.path.isfile(delete_files_on_startup_file) else 'w'

    # Create or append "delete on startup" file
    with open(delete_files_on_startup_file, write_type, encoding="utf8") as outfile:
        outfile.write(file_path + '\n')


def delete_and_rename_files_on_startup():
    if not os.path.isfile(delete_files_on_startup_file):
        return

    with open(delete_files_on_startup_file, 'r', encoding="utf8") as outfile:
        lines = outfile.readlines()

    # Delete the file immediately to allow it to be recreated if something fails
    os.remove(delete_files_on_startup_file)

    for path in lines:
        if not path:
            continue

        # Remove the line separator from the end of the path
        path = path[:-1]

        if os.path.isfile(path):
            try:
                os.remove(path)
                print('Removed file on startup', path)
            except OSError:
                print('Failed to remove file on startup', path)
                add_file_to_delete_on_startup(path)
                continue

        path_renamed = path + file_replacement_extension
        if os.path.isfile(path_renamed):
            os.rename(path_renamed, path)
            print('Renamed', path_renamed, 'to', path)


def set_ignored_version():
    # Create resources folder
    pathlib.Path(resources_dir).mkdir(exist_ok=True)

    # Create ignore file
    with open(ignore_ver_file, 'w', encoding="utf8") as outfile:
        outfile.write(latest_version_str)

    # Set ignored status
    global is_ignored_version
    is_ignored_version = True
    print('IGNORE VERSION ' + latest_version_str)


def check_ignored_version():
    if not os.path.isfile(ignore_ver_file):
        # print('IGNORE FILE NOT FOUND')
        return False

    # Read ignore file
    with open(ignore_ver_file, 'r', encoding="utf8") as outfile:
        version = outfile.read()

    # Check if the latest version matches the one in the ignore file
    if latest_version_str == version:
        print('Update ignored.')
        return True

    # Delete ignore version file if the latest version is not the version in the file
    try:
        os.remove(ignore_ver_file)
    except OSError:
        print("FAILED TO REMOVE IGNORE VERSION FILE")

    return False


def get_version_list(self, context):
    choices = []
    for version in version_list:
        # Only include compatible versions
        if is_version_compatible(version.version_string):
            # 1. Will be returned by context.scene
            # 2. Will be shown in lists
            # 3. will be shown in the hover description (below description)
            choices.append((version.version_string, version.version_display_string, version.version_display_string))
        else:
            # Add incompatible versions with a warning
            display_string = version.version_display_string + " (incompatible)"
            description = f"Version {version.version_string} is not compatible with Blender {'.'.join(str(x) for x in bpy.app.version)}"
            choices.append((version.version_string, display_string, description))

    bpy.types.Object.Enum = choices
    return bpy.types.Object.Enum


def get_user_preferences():
    return bpy.context.user_preferences if hasattr(bpy.context, 'user_preferences') else bpy.context.preferences
