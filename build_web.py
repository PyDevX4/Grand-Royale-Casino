"""
Build GRAND ROYALE CASINO for the web browser (with pygbag) and publish it on GitHub Pages.

    python build_web.py              build only  -> release/web_src/build/web
    python build_web.py --publish    build, then put it online at the address below

The web version is the same casino.py running in the browser, so it works on computers that can't install
anything - like a Chromebook - and on phones and tablets (see PHONE_HTML). Saves go in the browser's own
storage. Multiplayer works there with room codes; there are no auto-updates, but the page always serves the
newest version.

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


# Phones and tablets. Added to the page pygbag makes:
#  - a tap is a click (and the game sees exactly where), a drag scrolls (help pages, the stock list...)
#  - a hidden text box for the phone's keyboard: the game says where its text boxes are, tapping one opens the
#    keyboard, and what's typed goes to the game as key presses (window.gr_setfields / gr_takekeys)
#  - full screen on the first tap, and a "turn your phone sideways" screen in portrait
VIEWPORT = '<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, ' \
           'viewport-fit=cover">'
PHONE_HTML = r'''
<style>
  html, body { overscroll-behavior: none; -webkit-user-select: none; user-select: none; -webkit-touch-callout: none;
               -webkit-tap-highlight-color: transparent; }
  body.gr-touch, body.gr-touch canvas { touch-action: none; }
  #grkb { position: fixed; left: 0; bottom: 0; width: 2px; height: 2px; opacity: 0; font-size: 16px; border: 0;
          padding: 0; margin: 0; background: transparent; color: transparent; caret-color: transparent; z-index: 1; }
  #grturn { display: none; position: fixed; inset: 0; z-index: 2000000; background: #140c18; color: #e0b854;
            font: bold 24px arial, sans-serif; text-align: center; flex-direction: column; align-items: center;
            justify-content: center; gap: 16px; padding: 24px; }
  #grturn .phone { width: 46px; height: 78px; border: 5px solid #e0b854; border-radius: 10px;
                   animation: grspin 2.2s ease-in-out infinite; }
  @keyframes grspin { 0%, 25% { transform: rotate(0deg); } 55%, 100% { transform: rotate(-90deg); } }
  #grturn small { font: 16px arial, sans-serif; color: #c8c0d0; }
  #grturn button { margin-top: 10px; font: bold 16px arial, sans-serif; color: #ddd; background: #3a2e48;
                   border: 2px solid #7a6a90; border-radius: 10px; padding: 10px 22px; }
</style>
<input id="grkb" type="text" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"
       enterkeyhint="done" aria-hidden="true">
<div id="grturn"><div class="phone"></div><div>Turn your phone sideways</div>
  <small>Grand Royale is made for a wide screen.</small><button id="grturn_ok">PLAY ANYWAY</button></div>
<script>
(function () {
  var TOUCH = (window.matchMedia && matchMedia('(pointer: coarse)').matches) ||
              ('ontouchstart' in window && navigator.maxTouchPoints > 0);
  window.__gr_touch = TOUCH ? 1 : 0;
  window.gr_setfields = function () {};
  window.gr_takekeys = function () { return '[]'; };
  if (!TOUCH) return;
  document.body.classList.add('gr-touch');

  var kb = document.getElementById('grkb'), turn = document.getElementById('grturn');
  var fields = [], typing = false, keys = [], FILL = '~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~', prev = '';
  function canvas() { return document.getElementById('canvas'); }
  function started() { return window.MM && window.MM.UME; }          // pygbag's "the player has tapped once"

  // ---- the game says where its text boxes are, and takes what was typed ----
  window.gr_setfields = function (json) {
    var s = JSON.parse(json);
    fields = s.f || [];
    typing = !!s.t;
    if (!typing && document.activeElement === kb) kb.blur();
  };
  window.gr_takekeys = function () { var out = JSON.stringify(keys); keys = []; return out; };
  function refill() { kb.value = FILL; prev = FILL; try { kb.setSelectionRange(FILL.length, FILL.length); } catch (e) {} }
  kb.addEventListener('focus', refill);
  kb.addEventListener('input', function () {        // compare with before: works with every phone keyboard
    var v = kb.value, i = 0;
    while (i < v.length && i < prev.length && v[i] === prev[i]) i++;
    for (var k = 0; k < prev.length - i; k++) keys.push('\b');
    for (var j = i; j < v.length; j++) keys.push(v[j]);
    prev = v;
    if (v.length < 8 || v.length > 200) refill();
  });
  // keys typed into the hidden box are only for the game's queue (the game mustn't get them twice)
  ['keydown', 'keyup', 'keypress'].forEach(function (type) {
    window.addEventListener(type, function (e) {
      if (e.target !== kb) return;
      if (type === 'keydown' && (e.key === 'Enter' || e.keyCode === 13)) { keys.push('\n'); e.preventDefault(); }
      e.stopImmediatePropagation();
    }, true);
  });
  function inField(gx, gy) {
    for (var i = 0; i < fields.length; i++) {
      var f = fields[i];
      if (gx >= f[0] && gx <= f[0] + f[2] && gy >= f[1] && gy <= f[1] + f[3]) return true;
    }
    return false;
  }

  // ---- taps are clicks, drags scroll ----
  var active = null, startY = 0, lastY = 0, scrolling = false, acc = 0, triedFull = false;
  function toGame(t) {
    var r = canvas().getBoundingClientRect();
    return [(t.clientX - r.left) * 1280 / r.width, (t.clientY - r.top) * 720 / r.height];
  }
  function mouse(type, t, buttons) {
    canvas().dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, clientX: t.clientX,
      clientY: t.clientY, screenX: t.screenX, screenY: t.screenY, button: 0, buttons: buttons }));
  }
  function wheel(t, dy) {
    canvas().dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, view: window,
      clientX: t.clientX, clientY: t.clientY, deltaY: dy, deltaMode: 0 }));
  }
  function mine(e) {
    if (!started() || !canvas()) return false;                       // (let pygbag see the very first tap)
    var t = e.target;
    return !(t === kb || (t.closest && t.closest('#grturn')));
  }
  function find(list) {
    for (var i = 0; i < list.length; i++) if (list[i].identifier === active) return list[i];
    return null;
  }
  window.addEventListener('touchstart', function (e) {
    if (!mine(e)) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (active !== null) return;                                     // one finger at a time
    var t = e.changedTouches[0];
    active = t.identifier; startY = lastY = t.clientY; scrolling = false; acc = 0;
    mouse('mousemove', t, 0);
    mouse('mousedown', t, 1);
  }, { capture: true, passive: false });
  window.addEventListener('touchmove', function (e) {
    if (!mine(e)) return;
    e.preventDefault(); e.stopImmediatePropagation();
    var t = find(e.changedTouches);
    if (!t) return;
    if (!scrolling && Math.abs(t.clientY - startY) > 14) scrolling = true;
    if (scrolling) {
      acc += t.clientY - lastY;
      while (Math.abs(acc) >= 28) { wheel(t, acc > 0 ? -100 : 100); acc += acc > 0 ? -28 : 28; }
    }
    lastY = t.clientY;
    mouse('mousemove', t, 1);
  }, { capture: true, passive: false });
  function end(e) {
    if (!mine(e)) return;
    e.preventDefault(); e.stopImmediatePropagation();
    var t = find(e.changedTouches);
    if (!t) return;
    active = null;
    mouse('mouseup', t, 0);
    var g = toGame(t);                                              // the keyboard has to open inside the tap
    if (!scrolling && inField(g[0], g[1])) kb.focus({ preventScroll: true });
    else if (document.activeElement === kb && !scrolling) kb.blur();
    if (!triedFull) {                                                // full screen (and sideways, where allowed)
      triedFull = true;
      var el = document.documentElement, go = el.requestFullscreen || el.webkitRequestFullscreen;
      if (go && !document.fullscreenElement) {
        try {
          var p = go.call(el, { navigationUI: 'hide' });
          if (p && p.then) p.then(function () {
            if (screen.orientation && screen.orientation.lock) screen.orientation.lock('landscape').catch(function () {});
          }).catch(function () {});
        } catch (err) {}
      }
    }
  }
  window.addEventListener('touchend', end, { capture: true, passive: false });
  window.addEventListener('touchcancel', end, { capture: true, passive: false });
  ['pointerdown', 'pointermove', 'pointerup', 'pointercancel'].forEach(function (type) {   // (taps come once, above)
    window.addEventListener(type, function (e) {
      if (e.pointerType === 'touch' && mine(e)) e.stopImmediatePropagation();
    }, true);
  });

  // ---- held upright: ask for sideways ----
  var anyway = false;
  function check() {
    turn.style.display = (!anyway && window.innerHeight > window.innerWidth * 1.05) ? 'flex' : 'none';
  }
  document.getElementById('grturn_ok').addEventListener('click', function () { anyway = true; check(); });
  window.addEventListener('resize', check);
  window.addEventListener('orientationchange', function () { setTimeout(check, 300); });
  check();
})();
</script>
'''


def phone_page(out):
    """Add the phone and tablet support to the page pygbag made."""
    path = os.path.join(out, "index.html")
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    lines = [ln for ln in html.split("\n") if '<meta name="viewport"' not in ln]
    html = "\n".join(lines).replace('<meta charset="UTF-8">', '<meta charset="UTF-8">\n    ' + VIEWPORT, 1)
    if "</body>" not in html or VIEWPORT not in html:
        sys.exit("couldn't add the phone support to " + path)
    html = html.replace("</body>", PHONE_HTML + "\n</body>", 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


def make_music():
    """Turn the Jackpot song in the music folder (.wav or .mp3) into a small .ogg for the builds.
    Returns the folder it's in, or None."""
    found = [os.path.join(HERE, "music", n) for n in sorted(os.listdir(os.path.join(HERE, "music")))
             if n.lower().startswith("jackpot.") and n.lower().endswith((".wav", ".mp3"))]         if os.path.isdir(os.path.join(HERE, "music")) else []
    out_dir = os.path.join(HERE, "release", "music")
    out = os.path.join(out_dir, "jackpot.ogg")
    if not found:
        print("no Jackpot song in the music folder - building without it")
        return None
    src = found[0]
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(src):
        return out_dir
    try:
        import soundfile as sf              # pip install soundfile
    except ImportError:
        print("pip install soundfile to include the Jackpot song")
        return None
    os.makedirs(out_dir, exist_ok=True)
    info = sf.info(src)
    with sf.SoundFile(out, "w", samplerate=info.samplerate, channels=info.channels, format="OGG",
                      subtype="VORBIS") as dst:
        for block in sf.blocks(src, blocksize=48000, dtype="float32"):   # in pieces: one big write crashes
            dst.write(block)
    print("made %s (%.1f MB)" % (out, os.path.getsize(out) / 1e6))
    return out_dir


def build():
    shutil.rmtree(SRC, ignore_errors=True)
    os.makedirs(SRC)
    for name in ("casino.py", "version.py"):
        shutil.copy(os.path.join(HERE, name), SRC)
    with open(os.path.join(SRC, "main.py"), "w", encoding="utf-8") as fh:
        fh.write(MAIN_PY)
    music = make_music()
    if music:
        shutil.copytree(music, os.path.join(SRC, "music"))
    cmd = [sys.executable, "-m", "pygbag", "--build", "--title", "Grand Royale Casino", "--app_name",
           "grand_royale", "--width", "1280", "--height", "720", SRC]
    print(" ".join(cmd))
    if subprocess.call(cmd) != 0:
        sys.exit("pygbag failed")
    out = os.path.join(SRC, "build", "web")
    if not os.path.exists(os.path.join(out, "index.html")):
        sys.exit("pygbag finished but there's no index.html in " + out)
    phone_page(out)
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
