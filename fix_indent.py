"""Fix indentation in the generated fetch_mlb_stats.py entry point."""
import re

with open("fetch_mlb_stats.py", "r", encoding="utf-8") as f:
    content = f.read()

# Fix: strip leading whitespace from all top-level import lines
# and from the module import block
lines = content.split("\n")
fixed = []
inside_main = False

for line in lines:
    stripped = line.lstrip()
    # Track if we're inside def main(): body
    if stripped.startswith("def main():"):
        inside_main = True
        fixed.append(line)
        continue

    if inside_main:
        # Keep everything inside main as-is
        fixed.append(line)
        continue

    # Outside main: fix indentation on import/from lines and comments
    if stripped.startswith(("import ", "from ", "# ")):
        fixed.append(stripped)
    else:
        fixed.append(line)

with open("fetch_mlb_stats.py", "w", encoding="utf-8") as f:
    f.write("\n".join(fixed))

print("Fixed indentation in fetch_mlb_stats.py")

# Show first 30 lines to verify
with open("fetch_mlb_stats.py", "r", encoding="utf-8") as f:
    for i, line in enumerate(f, 1):
        if i <= 30:
            print(f"  {i:3d}: {line.rstrip()}")
