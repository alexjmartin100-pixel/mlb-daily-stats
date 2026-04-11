"""Show the TEAM_COLORS definition and tm() function in html_template.py,
plus all places where team badges are rendered."""

with open("html_template.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Show TEAM_COLORS block (around line 946)
print("=== TEAM_COLORS definition ===")
for i in range(940, min(975, len(lines))):
    print(f"  {i+1}: {lines[i].rstrip()}")

# Show the tm() function and surrounding code
print("\n=== tm() function / badge renderer ===")
for i, line in enumerate(lines):
    if "function tm(" in line or "function tm " in line or "const tm=" in line or "var tm=" in line:
        for j in range(max(0,i-2), min(len(lines), i+15)):
            print(f"  {j+1}: {lines[j].rstrip()}")
        break

# Show all places where team badges are generated (look for tm( usage)
print("\n=== All tm() calls ===")
for i, line in enumerate(lines):
    if "tm(" in line and "html" not in line[:10].lower():
        print(f"  {i+1}: {line.rstrip()[:130]}")

# Also check player_cards.py team display
print("\n=== player_cards.py team display ===")
with open("player_cards.py", "r", encoding="utf-8") as f:
    pc_lines = f.readlines()
for i, line in enumerate(pc_lines):
    if "d.team" in line or "TEAM_COLORS" in line or "teamColor" in line:
        print(f"  {i+1}: {line.rstrip()[:130]}")

# Check Season Leaders / Compare sections for team badge (line 2328)
print("\n=== Season Leaders / Compare team display ===")
for i in range(2320, min(2340, len(lines))):
    print(f"  {i+1}: {lines[i].rstrip()[:130]}")
