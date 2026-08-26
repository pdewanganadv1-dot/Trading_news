"""Inline the matplotlib SVG charts into the report template (theme-aware)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CH = ROOT / "outputs" / "charts"
tpl = (ROOT / "report" / "template.html").read_text()

REPL = {"#52514e": "var(--ink-2)", "#898781": "var(--muted)", "#d8d7d0": "var(--grid)",
        "#2a78d6": "var(--blue)", "#eb6834": "var(--orange)", "#1baf7a": "var(--aqua)",
        "#ffffff": "var(--surface)"}


def prep(name: str) -> str:
    s = (CH / f"{name}.svg").read_text()
    s = s[s.index("<svg"):]                                    # drop xml/doctype header
    s = re.sub(r"<metadata>.*?</metadata>", "", s, flags=re.S)
    for k, v in REPL.items():
        s = s.replace(k, v)
    # prefix ids so several charts can coexist in one document
    s = re.sub(r'id="([^"]+)"', lambda m: f'id="{name}-{m.group(1)}"', s)
    s = re.sub(r'url\(#([^)]+)\)', lambda m: f'url(#{name}-{m.group(1)})', s)
    s = re.sub(r'xlink:href="#([^"]+)"', lambda m: f'xlink:href="#{name}-{m.group(1)}"', s)
    # responsive sizing: keep viewBox, drop fixed width/height
    s = re.sub(r'<svg([^>]*?) width="[^"]*" height="[^"]*"', r'<svg\1', s, count=1)
    s = s.replace("<svg", '<svg role="img" preserveAspectRatio="xMidYMid meet"', 1)
    return s


out = re.sub(r"\{\{CHART:([a-z_]+)\}\}", lambda m: prep(m.group(1)), tpl)
dest = ROOT / "report" / "expiry_edge.html"
dest.write_text(out)
print(dest, len(out) // 1024, "KB")
