"""
Build GRAND ROYALE CASINO into a single Windows .exe, and publish updates (same setup as Cube Shooter).

    python build_exe.py                     build only
    python build_exe.py --bump --publish    bump the patch version (1.0.0 -> 1.0.1), build, and put it on GitHub
    python build_exe.py --minor --publish   same, but 1.0.x -> 1.1.0

Once a release is published, every built copy of the game picks it up by itself the next time it starts:
it downloads the new version in the background and restarts into it when you're in the lobby.

Publishing uses the GitHub CLI (gh), logged in as the owner of REPO.

Leaves "release/GrandRoyale.exe" -- one file, no Python or pygame needed to run it -- plus a zip and latest.json.
"""

import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import zipfile

from version import VERSION
from version import parse as version_parse

NAME = "Grand Royale Casino"
REPO = "PyDevX4/Grand-Royale-Casino"
ASSET = "GrandRoyale.exe"   # No spaces: GitHub rewrites spaces in asset names, which would break the download url
ENTRY = "casino.py"
ICON = "icon.ico"
HERE = os.path.dirname(os.path.abspath(__file__))

# What changed - shown on the GitHub release page. Edit before publishing if you like.
NOTES = "Coin Flip: call the EDGE for 20x, and DOUBLE OR NOTHING after every win. The game installs this update by itself the next time you open it."
DOWNLOAD_NOTE = "\n".join([
    "---",
    "**To play: download the .zip below.** Unzip it and run \"Grand Royale Casino.exe\" inside.",
    "",
    "Play money only - chips have no cash value and can't be exchanged for real money.",
    "",
    "The loose .exe and latest.json are what the game's own auto-updater uses -- you don't need them.",
])


def write_icon():
    """Draw a red-and-gold casino chip with a spade in the middle and save it as icon.ico."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import math
    import pygame
    pygame.init()
    size = 256
    c = size // 2
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.circle(surf, (120, 16, 24), (c, c), 124)
    pygame.draw.circle(surf, (190, 30, 40), (c, c), 116)
    for i in range(8):                                   # white edge inserts
        a = math.radians(i * 45)
        pts = [(c + r * math.cos(a + d), c + r * math.sin(a + d)) for r, d in
               ((116, -0.17), (116, 0.17), (88, 0.2), (88, -0.2))]
        pygame.draw.polygon(surf, (245, 245, 240), pts)
    pygame.draw.circle(surf, (235, 195, 60), (c, c), 80)
    pygame.draw.circle(surf, (190, 30, 40), (c, c), 72)
    # spade
    pygame.draw.circle(surf, (245, 215, 110), (c - 22, c + 4), 26)
    pygame.draw.circle(surf, (245, 215, 110), (c + 22, c + 4), 26)
    pygame.draw.polygon(surf, (245, 215, 110), [(c, c - 50), (c - 47, c + 8), (c + 47, c + 8)])
    pygame.draw.polygon(surf, (245, 215, 110), [(c, c + 10), (c - 16, c + 46), (c + 16, c + 46)])
    png = io.BytesIO()
    pygame.image.save(surf, png, "icon.png")
    data = png.getvalue()
    head = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(data), 22)
    with open(os.path.join(HERE, ICON), "wb") as fh:
        fh.write(head + entry + data)
    pygame.quit()


def bump_version(part="patch"):
    global VERSION
    path = os.path.join(HERE, "version.py")
    text = open(path, encoding="utf-8").read()
    major, minor, patch = version_parse(VERSION)[:3]
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    new = "%d.%d.%d" % (major, minor, patch)
    old_line = 'VERSION = "%s"' % VERSION
    if old_line not in text:
        sys.exit("could not find %s in version.py" % old_line)
    open(path, "w", encoding="utf-8").write(text.replace(old_line, 'VERSION = "%s"' % new))
    print("version %s -> %s" % (VERSION, new))
    VERSION = new
    return new


def find_gh():
    found = shutil.which("gh")
    if found:
        return found
    for guess in (os.path.join(os.environ.get("ProgramFiles", ""), "GitHub CLI", "gh.exe"),
                  os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", "gh.exe")):
        if guess and os.path.exists(guess):
            return guess
    return None


def publish(asset, manifest, bundle):
    gh = find_gh()
    if gh is None:
        sys.exit("the GitHub CLI (gh) is not installed: winget install --id GitHub.cli, then gh auth login")
    tag = "v" + VERSION
    exists = subprocess.call([gh, "release", "view", tag, "--repo", REPO],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    if exists:
        print("release %s exists -- replacing its files" % tag)
        cmd = [gh, "release", "upload", tag, asset, manifest, bundle, "--repo", REPO, "--clobber"]
    else:
        print("creating release %s" % tag)
        cmd = [gh, "release", "create", tag, asset, manifest, bundle, "--repo", REPO,
               "--title", "%s %s" % (NAME, VERSION), "--notes", NOTES + "\n\n" + DOWNLOAD_NOTE]
    if subprocess.call(cmd) != 0:
        sys.exit("gh failed -- nothing was published")
    print("\npublished %s. every copy of the game will update itself next time it starts." % VERSION)


def package(exe, publish_it=False):
    dist = os.path.dirname(exe)
    digest = hashlib.sha256()
    with open(exe, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    sha = digest.hexdigest()

    folder = "%s %s" % (NAME, VERSION)
    zip_path = os.path.join(dist, folder + ".zip")
    readme = "\r\n".join([
        NAME + "  v" + VERSION,
        "",
        'Double-click "%s.exe" to play.' % NAME,
        "",
        "Windows may warn that it does not recognise the file -- it is not signed.",
        "Click More info, then Run anyway.",
        "",
        "Keep this folder together: your chips and progress are saved to save.json next to the .exe.",
        "Play money only - chips have no cash value and can't be exchanged for real money.",
        "The game updates itself when a new version comes out.",
        "",
    ])
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe, "%s/%s.exe" % (folder, NAME))
        zf.writestr("%s/README.txt" % folder, readme)

    url = "https://github.com/%s/releases/latest/download/%s" % (REPO, ASSET)
    manifest = os.path.join(dist, "latest.json")
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump({"version": VERSION, "url": url, "sha256": sha, "notes": NOTES}, fh, indent=1)

    print("packed  %s  (%.1f MB)" % (zip_path, os.path.getsize(zip_path) / 1e6))
    print("wrote   %s  (sha256 %s)" % (manifest, sha))
    if publish_it:
        publish(exe, manifest, zip_path)
    else:
        print("\nto publish an update:  python build_exe.py --bump --publish")


def main():
    os.chdir(HERE)
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        return
    for part in ("major", "minor"):
        if "--" + part in args:
            bump_version(part)
            break
    else:
        if "--bump" in args:
            bump_version("patch")

    write_icon()
    # Build into "release" rather than "dist": if you're playing dist\GrandRoyale.exe, Windows won't let it be overwritten
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--distpath", "release",
           "--name", os.path.splitext(ASSET)[0], "--icon", ICON,
           "--hidden-import", "updater", "--hidden-import", "version", ENTRY]
    print(" ".join(cmd))
    if subprocess.call(cmd) != 0:
        sys.exit("PyInstaller failed")
    shutil.rmtree(os.path.join(HERE, "build"), ignore_errors=True)
    out = os.path.join(HERE, "release", ASSET)
    if not os.path.exists(out):
        sys.exit("build reported success but %s is missing" % out)
    print("\nbuilt %s  (%.1f MB)" % (out, os.path.getsize(out) / 1e6))
    package(out, publish_it="--publish" in args)


if __name__ == "__main__":
    main()
