#!/usr/bin/env python3
"""Assemble film2 into one self-contained file: out/landing-film.html.

    python3 build.py            build (warns about missing slides, which become placeholders)
    python3 build.py --strict   fail if any of the 9 slides is missing
    python3 build.py --ship     also copy the result to the studio's public/landing-film.html (integrator only)
"""
import pathlib, sys, json, shutil

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out" / "landing-film.html"
SHIP = HERE.parent / "public" / "landing-film.html"
SLIDES = [
    "00-opening", "01-engine", "02-connect", "03-secure", "04-reuse",
    "05-ask", "06-keep", "07-test", "08-close",
]
NAMES = ["Opening", "Engine", "Connect", "Secure", "Reuse", "Ask", "Keep", "Test", "Close"]


def placeholder(i: int) -> str:
    name = NAMES[i]
    return (
        "Film.slide({ index: %d, id: %s, name: %s, word: %s, dur: 1.2, kicker: %s, caption: %s });"
        % (i, json.dumps(name.lower()), json.dumps(name), json.dumps(name),
           json.dumps("%02d · %s" % (i, name)), json.dumps("(slide %s not built yet)" % SLIDES[i]))
    )


def main() -> int:
    strict = "--strict" in sys.argv
    shell = (HERE / "shell.html").read_text()
    css = (HERE / "kit.css").read_text()
    js = (HERE / "kit.js").read_text()
    parts, missing = [], []
    for i, stem in enumerate(SLIDES):
        f = HERE / "slides" / f"{stem}.js"
        if f.exists():
            src = f.read_text()
            if "</script" in src.lower():
                print(f"error: {f.name} contains '</script' (escape it as '<\\/script')", file=sys.stderr)
                return 2
            parts.append(f"<script data-slide=\"{stem}\">\n{src}\n</script>")
        else:
            missing.append(stem)
            parts.append(f"<script data-slide=\"{stem}\" data-placeholder>{placeholder(i)}</script>")
    if missing:
        print("missing slides (placeholders used): " + ", ".join(missing), file=sys.stderr)
        if strict:
            return 1
    html = shell.replace("/*@KIT_CSS*/", css).replace("/*@KIT_JS*/", js).replace("<!--@SLIDES-->", "\n".join(parts))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    print(f"wrote {OUT} ({len(html.encode()) / 1024:.0f} KB)")
    if "--ship" in sys.argv:
        if missing:
            print("refusing to ship with missing slides", file=sys.stderr)
            return 1
        shutil.copyfile(OUT, SHIP)
        print(f"shipped to {SHIP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
