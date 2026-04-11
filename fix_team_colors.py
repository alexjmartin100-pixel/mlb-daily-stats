"""
Fix team colors across the entire dashboard:
1. Add team abbreviation aliases (ATH, CHW, KCR, TBR, ANA, FLA, MON, WSN, etc.)
2. Player Cards tab: use TEAM_COLORS for team abbreviation
3. Roster player list: use tm() for team badge
"""

# ═══════════════════════════════════════════════════════════════════
# Fix html_template.py
# ═══════════════════════════════════════════════════════════════════

with open("html_template.py", "r", encoding="utf-8") as f:
    content = f.read()

changes = []

# 1. Add aliases after the TEAM_COLORS closing brace
old_colors_end = """};\nconst tm = t => {"""

if old_colors_end in content:
    new_colors_end = """};\n// Aliases for alternate abbreviations\nTEAM_COLORS.CHW=TEAM_COLORS.CWS;\nTEAM_COLORS.KCR=TEAM_COLORS.KC;\nTEAM_COLORS.TBR=TEAM_COLORS.TB;\nTEAM_COLORS.ANA=TEAM_COLORS.LAA;\nTEAM_COLORS.FLA=TEAM_COLORS.MIA;\nTEAM_COLORS.MON=TEAM_COLORS.WSH;\nTEAM_COLORS.WSN=TEAM_COLORS.WSH;\nTEAM_COLORS.ATH=['#003831','#EFB21E'];\nconst tm = t => {"""
    content = content.replace(old_colors_end, new_colors_end)
    changes.append("1. Added team abbreviation aliases (ATH, CHW, KCR, TBR, etc.)")
else:
    changes.append("1. SKIPPED aliases (pattern not found)")

# 2. Fix roster player list badge (line ~2328)
old_roster_badge = """const badge=p.team?`<span style="font-size:.68rem;color:var(--muted);margin-left:5px">${p.team}</span>`:'';"""
new_roster_badge = """const badge=p.team?`<span style="margin-left:5px">${tm(p.team)}</span>`:'';"""

if old_roster_badge in content:
    content = content.replace(old_roster_badge, new_roster_badge)
    changes.append("2. Roster player list: now uses tm() for team badges")
else:
    changes.append("2. SKIPPED roster badge (pattern not found)")

with open("html_template.py", "w", encoding="utf-8") as f:
    f.write(content)

# ═══════════════════════════════════════════════════════════════════
# Fix player_cards.py
# ═══════════════════════════════════════════════════════════════════

with open("player_cards.py", "r", encoding="utf-8") as f:
    pc_content = f.read()

# Replace the plain white team text with a colored badge using TEAM_COLORS
# Current: '<span style="font-size:.82rem;color:#ffffff;font-weight:700;text-shadow:0 0 0 #fff">' + (d.team||'–') + '</span>'
old_team_span = """'<span style="font-size:.82rem;color:#ffffff;font-weight:700;text-shadow:0 0 0 #fff">' + (d.team||'\\xe2\\x80\\x93') + '</span>'"""

# Try more flexible approach - find any span containing d.team with a fixed color
import re
team_span_pat = re.compile(
    r"'<span style=\"[^\"]*color:#[0-9a-fA-F]{3,6}[^\"]*\">' \+ \(d\.team\|\|'[^']*'\) \+ '</span>'"
)

match = team_span_pat.search(pc_content)
if match:
    old_span = match.group(0)
    # Replace with a tm()-style badge using TEAM_COLORS (which is available in the global scope)
    new_span = """(function(){var tc=typeof TEAM_COLORS!=='undefined'?TEAM_COLORS[d.team]:null;if(tc)return '<span style=\"display:inline-block;padding:1px 8px;border-radius:4px;font-size:.78rem;font-weight:700;background:'+tc[0]+';color:'+tc[1]+';border:1px solid '+tc[0]+'44\">'+d.team+'</span>';return '<span style=\"font-size:.78rem;font-weight:700;color:#ccc\">'+(d.team||'\\u2013')+'</span>';})()"""
    pc_content = pc_content.replace(old_span, new_span)
    changes.append("3. Player Cards: team abbreviation now uses TEAM_COLORS badge")
else:
    # Try an even simpler search
    for pat in [
        "color:#ffffff;font-weight:700",
        "color:#fff;font-weight:600",
        "color:#fff;font-weight:700",
    ]:
        if pat in pc_content:
            # Find the full line
            for i, line in enumerate(pc_content.split("\n")):
                if pat in line and "d.team" in line:
                    changes.append(f"3. Found team span at line {i+1}: {line.strip()[:80]}")
                    # Replace this specific line
                    old_line = line
                    new_line = line.replace(
                        f"'<span style=\"font-size:.82rem;{pat};text-shadow:0 0 0 #fff\">' + (d.team||",
                        "(function(){var tc=typeof TEAM_COLORS!=='undefined'?TEAM_COLORS[d.team]:null;if(tc)return '<span style=\"display:inline-block;padding:1px 8px;border-radius:4px;font-size:.78rem;font-weight:700;background:'+tc[0]+';color:'+tc[1]+'\">'+d.team+'</span>';return '<span style=\"font-size:.78rem;font-weight:700;color:#ccc\">'+(d.team||"
                    )
                    pc_content = pc_content.replace(old_line, new_line)
                    changes.append("3. Player Cards: replaced team span with TEAM_COLORS lookup")
                    break
            break
    else:
        changes.append("3. SKIPPED player cards team (pattern not found)")

with open("player_cards.py", "w", encoding="utf-8") as f:
    f.write(pc_content)

# ═══════════════════════════════════════════════════════════════════
# Report
# ═══════════════════════════════════════════════════════════════════

print("Changes:")
for c in changes:
    print(f"  {c}")

# Verify syntax
print("\nVerifying syntax...")
for f in ["html_template.py", "player_cards.py"]:
    try:
        with open(f, "r", encoding="utf-8") as fh:
            compile(fh.read(), f, "exec")
        print(f"  {f}: OK")
    except SyntaxError as e:
        print(f"  {f}: SYNTAX ERROR at line {e.lineno}: {e.msg}")
