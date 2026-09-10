"""Remove CSS rules whose selectors only target classes that no template, script or view uses.

Usage: python scripts/css_prune.py static_src/css/system.css [--write]
Mixed selector lists keep their live selectors; @media/@keyframes blocks are kept when non-empty.
"""
import re, sys, pathlib

root = pathlib.Path(__file__).resolve().parents[1]
KEEP_PREFIXES = ("htmx", "is-", "has-", "login-state", "reveal-step", "mix-", "status-", "nav-", "password-eye", "login-")


def used_classes():
    html = "\n".join(p.read_text(encoding="utf-8") for p in (root / "templates").rglob("*.html"))
    js = (root / "static_src/js/app.js").read_text(encoding="utf-8")
    py = "\n".join(p.read_text(encoding="utf-8") for p in (root / "apps").rglob("*.py"))
    used = set()
    for m in re.finditer(r'class="([^"]*)"', html):
        used.update(t for t in re.split(r"[\s{}%|:'\"]+", m.group(1)) if t)
    for m in re.finditer(r'{% block body_class %}([^{]*){% endblock %}', html):
        used.update(m.group(1).split())
    used |= set(re.findall(r'["\'\.]([a-zA-Z][\w-]+)', js))
    used |= set(re.findall(r'["\']([a-z][\w-]+)["\']', py))
    return used


def selector_is_dead(selector, used):
    classes = re.findall(r"\.(-?[_a-zA-Z][\w-]*)", selector)
    dead = [c for c in classes if c not in used and not c.startswith(KEEP_PREFIXES)]
    return bool(dead)


def prune(css, used):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out, i, n = [], 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace == -1:
            out.append(css[i:]); break
        prelude = css[i:brace]
        if prelude.strip().startswith("@") and not prelude.strip().startswith(("@font-face", "@page")):
            depth, j = 1, brace + 1
            while j < n and depth:
                depth += {"{": 1, "}": -1}.get(css[j], 0); j += 1
            inner = prune(css[brace + 1:j - 1], used) if not prelude.strip().startswith("@keyframes") else css[brace + 1:j - 1]
            if inner.strip():
                out.append(f"{prelude}{{{inner}}}")
            i = j
            continue
        end = css.find("}", brace)
        body = css[brace + 1:end]
        selectors = [s.strip() for s in prelude.split(",") if s.strip()]
        alive = [s for s in selectors if not selector_is_dead(s, used)]
        if alive:
            lead = re.match(r"\s*", prelude).group(0)
            out.append(f"{lead}{','.join(alive)}{{{body}}}")
        i = end + 1
    return "".join(out)


if __name__ == "__main__":
    path = pathlib.Path(sys.argv[1])
    css = path.read_text(encoding="utf-8")
    result = prune(css, used_classes())
    result = re.sub(r"\n{3,}", "\n\n", result)
    print(f"{path.name}: {len(css)} -> {len(result)} bytes")
    if "--write" in sys.argv:
        path.write_text(result, encoding="utf-8")
