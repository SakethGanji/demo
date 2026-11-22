"""Distinct exception signatures in an API log: (exception class+message head, innermost app frame, first app frame).
    python logsig.py <api.log>"""
import collections, re, sys
txt = open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()
sigs = collections.Counter(); ex = {}
frames = []; intb = False
for ln in txt:
    if ln.startswith("Traceback (most recent call last)") or ln.startswith("  + Exception Group Traceback"):
        intb = True; frames = []; continue
    if intb and ln.strip().startswith("File ") and "/backend/app/" in ln:
        frames.append(re.sub(r'.*/backend/app/', 'app/', ln.strip()))
    elif intb and re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Group)\b", ln):
        msg = re.sub(r"'[^']{12,}'", "'…'", ln)[:140]
        msg = re.sub(r"\d{3,}", "N", msg)
        key = (msg.split(":")[0], frames[-1] if frames else "-", frames[0] if frames else "-")
        sigs[key] += 1; ex.setdefault(key, msg)
        intb = False
for k, n in sigs.most_common():
    if k[0].startswith("fastapi.exceptions.RequestValidationError"):
        continue
    print(f"{n:6d} | {ex[k]} | inner {k[1]} | outer {k[2]}")
