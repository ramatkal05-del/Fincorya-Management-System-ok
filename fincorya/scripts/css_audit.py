import re, pathlib
root = pathlib.Path(__file__).resolve().parents[1]
html = "\n".join(p.read_text(encoding="utf-8") for p in (root / "templates").rglob("*.html"))
js = (root / "static_src/js/app.js").read_text(encoding="utf-8")
py = "\n".join(p.read_text(encoding="utf-8") for p in (root / "apps").rglob("*.py"))
used = set()
for m in re.finditer(r'class="([^"]*)"', html):
    for token in re.split(r"[\s{}%|:'\"]+", m.group(1)):
        if token and not token.startswith(("{", "}", "if", "else", "endif", "request", "message")):
            used.add(token)
used |= set(re.findall(r'["\'\.]([a-zA-Z][\w-]+)', js))
used |= set(re.findall(r'["\']([a-z][\w-]+)["\']', py))
for f in sorted((root / "static_src/css").glob("*.css")):
    css = f.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    classes = set(re.findall(r"\.([a-zA-Z_][\w-]*)", re.sub(r"\{[^{}]*\}", "", css)))
    dead = sorted(c for c in classes if c not in used and not c.startswith(("htmx", "is-", "has-")))
    print(f"\n== {f.name}: {len(classes)} classes, {len(dead)} unused ({len(css)//1024} KB)")
    print(", ".join(dead[:80]))
