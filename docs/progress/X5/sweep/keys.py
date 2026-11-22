"""Map every (METHOD, path) to the body/query keys its handler source reads (heuristic); writes keys.json."""
import inspect, json, re, sys
from fastapi.routing import APIRoute
from app.main import create_app
app = create_app()
out = {}
RX = re.compile(r"""\.get\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']|\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]""")
def walk(routes, prefix=""):
    for r in routes:
        if isinstance(r, APIRoute):
            try:
                src = inspect.getsource(r.endpoint)
            except Exception:
                src = ""
            keys = sorted({a or b for a, b in RX.findall(src)})
            for m in r.methods:
                out.setdefault(f"{m} {prefix}{r.path}", keys)
        elif type(r).__name__ == "_IncludedRouter":
            walk(r.original_router.routes, prefix + (r.include_context.prefix or ""))
walk(app.routes)
print(len(out))
json.dump(out, open(sys.argv[1], "w"), indent=0)
