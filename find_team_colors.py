"""Find how team badges/colors are currently defined across all files."""
import os

FILES = [f for f in os.listdir(".") if f.endswith(".py") and os.path.isfile(f)]

for filepath in sorted(FILES):
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    hits = []
    for i, line in enumerate(lines):
        lower = line.lower()
        if any(x in lower for x in ["team_color", "teamcolor", "team_bg", "teambg",
                "badge", "team-badge", "team_badge", "'ari'", '"ari"',
                "color_map", "colormap", "bg_map", "bgmap"]):
            hits.append((i+1, line.rstrip()))
        # Also catch inline team color definitions
        if ("background" in lower and any(t in line for t in ["ARI", "ATL", "BAL", "BOS", "CHC", "CLE"])
            and (":" in line or "=" in line)):
            hits.append((i+1, line.rstrip()))

    if hits:
        print(f"\n{'='*60}")
        print(f"  {filepath}")
        print(f"{'='*60}")
        for num, text in hits:
            print(f"  {num:5d}: {text[:120]}")

# Also check html_template.py specifically for team badge CSS/JS
print(f"\n{'='*60}")
print(f"  html_template.py - team badge search")
print(f"{'='*60}")
with open("html_template.py", "r", encoding="utf-8") as f:
    content = f.read()
    lines = content.split("\n")

for i, line in enumerate(lines):
    if any(x in line for x in ["teamBg", "team_bg", "TEAM_BG", "TeamBg",
            "teamColor", "team_color", "TEAM_COLOR",
            "badge", ".tm-", "tm-badge"]):
        print(f"  {i+1:5d}: {line.rstrip()[:130]}")
