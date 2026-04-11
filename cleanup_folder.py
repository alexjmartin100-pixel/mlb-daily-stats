"""
Clean up the MLB Daily Results folder.
Remove one-off fix scripts, diagnostic files, old batch files, and temp data.
Keep only the core project files.
"""
import os, shutil

# Files to KEEP (core project)
KEEP = {
    # Source modules
    "config.py", "utils.py", "fangraphs.py", "data_fetch.py",
    "batting_leaderboard.py", "pitching_leaderboard.py",
    "player_cards.py", "html_template.py", "fantasy.py",
    "fetch_mlb_stats.py",
    # Config / deploy
    "requirements.txt", "firebase.json", "manifest.json", "sw.js",
    "icon-192.png", "icon-512.png",
    # Output
    "mlb_daily_stats.html",
    # Auth
    "fg_cookie.txt",
    # Docs
    "SETUP_FIREBASE.md", "SETUP_GITHUB.md",
    # The one batch file the user actually uses
    "push_update.bat",
    # This cleanup script itself (will delete after)
    "cleanup_folder.py",
}

# Directories to KEEP
KEEP_DIRS = {".github", ".firebaserc", ".git", "__pycache__"}

removed = []
kept = []

for name in sorted(os.listdir(".")):
    path = os.path.join(".", name)

    # Skip hidden dirs and kept dirs
    if name.startswith(".") or name in KEEP_DIRS:
        kept.append(name)
        continue

    if name in KEEP:
        kept.append(name)
        continue

    if os.path.isdir(path):
        if name == "__pycache__":
            shutil.rmtree(path)
            removed.append(f"  {name}/ (directory)")
        else:
            kept.append(name)
        continue

    # Remove everything else
    try:
        os.remove(path)
        removed.append(f"  {name}")
    except Exception as e:
        print(f"  Could not remove {name}: {e}")

print(f"REMOVED {len(removed)} files:")
for r in removed:
    print(r)

print(f"\nKEPT {len(kept)} items:")
for k in sorted(kept):
    print(f"  {k}")
