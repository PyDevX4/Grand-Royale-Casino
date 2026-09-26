"""
Auto-update for Grand Royale Casino, the same way Cube Shooter and Until Dawn do it.

  * Every release on GitHub carries three files (build_exe.py uploads them):
        latest.json       the manifest: version, download url, sha256, notes
        GrandRoyale.exe   the build itself
        Grand Royale Casino <version>.zip   what a person downloading by hand should take

  * A built copy of the game checks latest.json when it starts. If the version there is newer, it downloads the
    new .exe in the background, checks it against the sha256, and swaps it in when you're in the lobby.

Two rules, because an auto-updater downloads a program and runs it:
  * HTTPS only, for the manifest and the download.
  * The download must match the manifest's SHA-256, or it is deleted instead of installed.

Windows can't overwrite a running .exe, so installing writes a small batch file that waits for the game to
close, moves the new file over the old one, starts it again (through Explorer) and deletes itself.

Running from the .py source (not a built .exe) the updater does nothing at all.
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

from version import VERSION, is_newer

FROZEN = getattr(sys, "frozen", False)
CAN_INSTALL = os.name == "nt"  # The swap uses a Windows batch file
MANIFEST_URL = "https://github.com/PyDevX4/Grand-Royale-Casino/releases/latest/download/latest.json"
EXE_NAME = "GrandRoyale.exe"
TIMEOUT = 6.0                  # Seconds; a slow host must never hold up the game
MAX_BYTES = 200 * 1024 * 1024  # A build is ~20-30 MB; refuse anything absurd
USER_AGENT = "GrandRoyale/%s" % VERSION


def app_dir():
    """Where the game lives from the player's point of view: next to the .exe when built, else this folder."""
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


class Update(object):
    def __init__(self, version, url, sha256, notes=""):
        self.version = version
        self.url = url
        self.sha256 = (sha256 or "").lower()
        self.notes = notes or ""


def _log(msg):
    """A breadcrumb trail next to the .exe, since a windowed build has no console to show errors in."""
    try:
        with open(os.path.join(app_dir(), "update.log"), "a") as fh:
            fh.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _get(url, timeout=TIMEOUT):
    if not url.lower().startswith("https://"):
        raise ValueError("refusing a non-HTTPS url: %r" % url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def check():
    """Ask the manifest whether there is anything newer. None if not (or if anything at all goes wrong)."""
    if not MANIFEST_URL or not FROZEN:
        return None
    _log("check: version %s, asking %s" % (VERSION, MANIFEST_URL))
    try:
        with _get(MANIFEST_URL) as resp:
            data = json.loads(resp.read(64 * 1024).decode("utf-8"))
        version = str(data.get("version", ""))
        url = str(data.get("url", ""))
        sha = str(data.get("sha256", ""))
        if not is_newer(version):
            _log("check: latest is %s, nothing newer" % version)
            return None
        if not url.lower().startswith("https://") or len(sha) != 64:
            _log("check: manifest for %s is unusable" % version)
            return None
        _log("check: %s is available" % version)
        return Update(version, url, sha, str(data.get("notes", "")))
    except Exception as exc:
        _log("check: failed -- %s: %s" % (type(exc).__name__, exc))
        return None


def download(update, progress=None):
    """Fetch the new build next to the current one, checked against its hash. Returns the staged path or None."""
    dest = os.path.join(app_dir(), "GrandRoyale.new.exe")
    part = dest + ".part"
    digest = hashlib.sha256()
    _log("download: %s" % update.url)
    try:
        with _get(update.url, timeout=30.0) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            if total > MAX_BYTES:
                return None
            got = 0
            with open(part, "wb") as fh:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > MAX_BYTES:
                        raise ValueError("download too large")
                    digest.update(chunk)
                    fh.write(chunk)
                    if progress:
                        progress(got, total)
        if digest.hexdigest().lower() != update.sha256:
            _log("download: CHECKSUM MISMATCH -- deleted")
            os.remove(part)
            return None
        if os.path.exists(dest):
            os.remove(dest)
        os.rename(part, dest)
        _log("download: %d bytes, checksum ok" % got)
        return dest
    except Exception as exc:
        _log("download: failed -- %s: %s" % (type(exc).__name__, exc))
        for path in (part, dest):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        return None


def tidy():
    """Clear leftovers from an update that didn't finish."""
    if not FROZEN:
        return
    for name in ("GrandRoyale.new.exe", "GrandRoyale.new.exe.part", "update.bat"):
        path = os.path.join(app_dir(), name)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def install(staged):
    """Swap the staged build in and restart, using a batch file that outlives the game. True if it started."""
    if not CAN_INSTALL or not FROZEN or not staged or not os.path.exists(staged):
        return False
    exe = os.path.abspath(sys.executable)
    script = os.path.join(app_dir(), "update.bat")
    # Keep trying the move: Windows refuses while the game still has the file open, so a failed move just means
    # "not closed yet". ping is the sleep that works without a console window. Explorer starts the new build so
    # PyInstaller's bootloader isn't confused by being launched from cmd.
    body = "\r\n".join([
        "@echo off",
        "setlocal",
        'set "SRC=%s"' % staged,
        'set "DST=%s"' % exe,
        "set /a tries=0",
        ":retry",
        "set /a tries+=1",
        'move /y "%SRC%" "%DST%" >nul 2>&1',
        'if not exist "%SRC%" goto done',
        "if %tries% geq 90 goto giveup",
        "ping -n 2 127.0.0.1 >nul",
        "goto retry",
        ":done",
        'explorer.exe "%DST%"',
        "ping -n 3 127.0.0.1 >nul",
        'del "%~f0"',
        "exit /b 0",
        ":giveup",
        'del /q "%SRC%" >nul 2>&1',
        'explorer.exe "%DST%"',
        "ping -n 3 127.0.0.1 >nul",
        'del "%~f0"',
        "",
    ])
    try:
        with open(script, "w", newline="") as fh:
            fh.write(body)
        subprocess.Popen(["cmd", "/c", script], creationflags=0x08000000)  # CREATE_NO_WINDOW
        _log("install: swap script started, closing the game for the handover")
        return True
    except Exception as exc:
        _log("install: failed -- %s: %s" % (type(exc).__name__, exc))
        return False


class AutoUpdater(object):
    """What the game talks to: starts the check, downloads anything newer in the background, and reports progress.
    Only plain fields are set from the background thread, so reading them each frame is safe."""

    def __init__(self):
        self.update = None     # The Update once one is found
        self.state = ""        # "", "downloading", "ready", "failed"
        self.progress = 0.0    # 0..1 while downloading
        self.staged = None     # Path of the verified new .exe once state is "ready"

    def start(self):
        if not FROZEN:
            return
        tidy()
        threading.Thread(target=self._run, name="casino-update", daemon=True).start()

    def _run(self):
        found = check()
        if not found or not CAN_INSTALL:
            return
        self.update = found
        self.state = "downloading"

        def progress(got, total):
            self.progress = (got / float(total)) if total else 0.0
        staged = download(found, progress)
        if staged:
            self.staged = staged
            self.state = "ready"
        else:
            self.state = "failed"
