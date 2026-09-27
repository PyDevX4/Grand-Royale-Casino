"""
The one place the version number lives.

`build_exe.py --bump` raises it before a release, stamps it into the update manifest, and `updater.py`
compares it against whatever the newest release says is current.
"""

VERSION = "1.0.6"


def parse(text):
    """"1.2.3" -> (1, 2, 3). Junk sorts lowest rather than raising."""
    out = []
    for part in str(text or "").split(".")[:4]:
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def is_newer(candidate, current=VERSION):
    return parse(candidate) > parse(current)
