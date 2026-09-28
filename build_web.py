"""
Build GRAND ROYALE CASINO for the web browser (with pygbag) and publish it on GitHub Pages.

    python build_web.py              build only  -> release/web_src/build/web
    python build_web.py --publish    build, then put it online at the address below

The web version is the same casino.py running in the browser, so it works on computers that can't install
anything - like a Chromebook. Saves go in the browser's own storage. Multiplayer and auto-updates are off
there (a web page can't do either), but the page always serves the newest version.

    https://pydevx4.github.io/Grand-Royale-Casino/

build_exe.py --publish runs this too, so the .exe and the web version stay the same version.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "PyDevX4/Grand-Royale-Casino"
URL = "https://pydevx4.github.io/Grand-Royale-Casino/"
SRC = os.path.join(HERE, "release", "web_src")

MAIN_PY = '''"""Browser entry point: pygbag runs this, and the game loop hands control back to the page every frame."""
import asyncio

import pygame

import casino


async def main():
    pygame.init()
    screen = pygame.display.set_mode((casino.W, casino.H))
    screen.fill((10, 8, 12))
    text = pygame.font.Font(None, 54).render("Shuffling the decks...", True, (230, 190, 90))
    screen.blit(text, text.get_rect(center=(casino.W // 2, casino.H // 2)))
    pygame.display.flip()
    await asyncio.sleep(0.2)             # let the page show that before the (slow) start-up
    app = casino.App()
    for _ in app.frames():
        await asyncio.sleep(0)

asyncio.run(main())
'''


def build():
    shutil.rmtree(SRC, ignore_errors=True)
    os.makedirs(SRC)
    for name in ("casino.py", "version.py"):
        shutil.copy(os.path.join(HERE, name), SRC)
    with open(os.path.join(SRC, "main.py"), "w", encoding="utf-8") as fh:
        fh.write(MAIN_PY)
    cmd = [sys.executable, "-m", "pygbag", "--build", "--title", "Grand Royale Casino", "--app_name",
           "grand_royale", "--width", "1280", "--height", "720", SRC]
    print(" ".join(cmd))
    if subprocess.call(cmd) != 0:
        sys.exit("pygbag failed")
    out = os.path.join(SRC, "build", "web")
    if not os.path.exists(os.path.join(out, "index.html")):
        sys.exit("pygbag finished but there's no index.html in " + out)
    print("\nbuilt the web version in", out)
    return out


def publish(out):
    """Force-push the built site to the gh-pages branch, which GitHub Pages serves."""
    site = tempfile.mkdtemp(prefix="grand_royale_web_")
    try:
        for name in os.listdir(out):
            src = os.path.join(out, name)
            (shutil.copytree if os.path.isdir(src) else shutil.copy)(src, os.path.join(site, name))
        open(os.path.join(site, ".nojekyll"), "w").close()
        git = ["git", "-C", site]
        subprocess.check_call(git + ["init", "-q", "-b", "gh-pages"])
        subprocess.check_call(git + ["config", "user.name", "PyDevX4"])
        subprocess.check_call(git + ["config", "user.email", "PyDevX4@users.noreply.github.com"])
        subprocess.check_call(git + ["add", "-A"])
        subprocess.check_call(git + ["commit", "-q", "-m", "Web version"])
        subprocess.check_call(git + ["push", "-q", "-f", "https://github.com/%s.git" % REPO, "gh-pages"])
    finally:
        shutil.rmtree(site, ignore_errors=True)
    # switch GitHub Pages on for the branch (only needed the first time; harmless after)
    subprocess.call(["gh", "api", "-X", "POST", "repos/%s/pages" % REPO, "-f", "source[branch]=gh-pages",
                     "-f", "source[path]=/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("\npublished the web version:", URL, "(GitHub can take a minute or two to update it)")


if __name__ == "__main__":
    site = build()
    if "--publish" in sys.argv[1:]:
        publish(site)
