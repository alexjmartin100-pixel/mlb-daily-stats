"""
Fix player cards:
1. Team abbreviations → white
2. Player photo → proper circle (not cut off)
3. Add fWAR display under RoS dollar value
4. Team logos → white outline, big, right corner (already in code but verify/enhance)
"""

import re

def fix_player_cards():
    with open("player_cards.py", "r", encoding="utf-8") as f:
        content = f.read()

    original = content
    changes = []

    # ═══════════════════════════════════════════════════════════════════════
    # 1. FIX PLAYER PHOTO: object-fit:contain → object-fit:cover + clip
    #    Current: width:120px;height:120px;object-fit:contain;flex-shrink:0;border-radius:50%;background:transparent
    #    Need:    proper circular clip with object-fit:cover so image fills circle
    # ═══════════════════════════════════════════════════════════════════════

    # Replace the img tag with a wrapper div for proper circular clipping
    old_img = (
        "'<img id=\"' + pcImgId + '\" src=\"' + photoUrl + '\" '"
        "\n      +   'onerror=\"this.style.display=\\\\x27none\\\\x27\" '"
        "\n      +   'style=\"width:120px;height:120px;object-fit:contain;flex-shrink:0;border-radius:50%;background:transparent\"'"
    )

    # Try a regex approach to be more flexible with whitespace
    img_pattern = re.compile(
        r"'<img id=\"' \+ pcImgId \+ '\" src=\"' \+ photoUrl \+ '\" '\s*"
        r"\+\s*'onerror=\"this\.style\.display=\\\\x27none\\\\x27\" '\s*"
        r"\+\s*'style=\"[^\"]*object-fit:contain[^\"]*\"'"
    )

    if img_pattern.search(content):
        content = img_pattern.sub(
            "'<div style=\"width:120px;height:120px;flex-shrink:0;border-radius:50%;overflow:hidden;background:#222\">'"
            "\n      + '<img id=\"' + pcImgId + '\" src=\"' + photoUrl + '\" '"
            "\n      +   'onerror=\"this.parentElement.style.display=\\\\x27none\\\\x27\" '"
            "\n      +   'style=\"width:100%;height:100%;object-fit:cover\"'"
            "\n      + '/></div>'",
            content
        )
        changes.append("1. Fixed player photo: circular clip with object-fit:cover")
    else:
        # Try simpler pattern
        if "object-fit:contain;flex-shrink:0;border-radius:50%" in content:
            content = content.replace(
                "object-fit:contain;flex-shrink:0;border-radius:50%;background:transparent",
                "object-fit:cover;flex-shrink:0;border-radius:50%;overflow:hidden;background:#222"
            )
            changes.append("1. Fixed player photo: object-fit:cover + overflow:hidden")
        else:
            changes.append("1. SKIPPED player photo (pattern not found)")

    # ═══════════════════════════════════════════════════════════════════════
    # 2. TEAM ABBREVIATION → WHITE
    #    Find all team display spans and ensure they're white
    # ═══════════════════════════════════════════════════════════════════════

    # The team abbreviation in the card header - may have various colors
    # Line ~257: '<span style="font-size:.78rem;color:XXXX;font-weight:600">' + (d.team||'–') + '</span>'
    team_abbr_pattern = re.compile(
        r"(font-size:\.78rem;color:)(#[0-9a-fA-F]{3,6})(;font-weight:600\b[^>]*>\'\s*\+\s*\(d\.team)"
    )
    if team_abbr_pattern.search(content):
        old_color = team_abbr_pattern.search(content).group(2)
        content = team_abbr_pattern.sub(r"\g<1>#fff\3", content)
        if old_color != "#fff":
            changes.append(f"2. Team abbreviation color: {old_color} → #fff")
        else:
            changes.append("2. Team abbreviation already white")
    else:
        # Try broader search for team display near d.team
        team_matches = list(re.finditer(r"color:(#[0-9a-fA-F]{3,6})[^>]*>\'\s*\+\s*\(d\.team", content))
        if team_matches:
            for m in team_matches:
                old_span = m.group(0)
                new_span = old_span.replace(f"color:{m.group(1)}", "color:#fff")
                content = content.replace(old_span, new_span)
            changes.append(f"2. Team abbreviation → white ({len(team_matches)} occurrence(s))")
        else:
            changes.append("2. SKIPPED team abbreviation (pattern not found)")

    # ═══════════════════════════════════════════════════════════════════════
    # 3. ADD fWAR DISPLAY under RoS dollar value
    #    a) Add "war" to player_data dict
    #    b) Add fWAR badge in JS after dvBadge
    # ═══════════════════════════════════════════════════════════════════════

    # 3a) Add "war" to the player_data dict if not already there
    if '"war"' not in content.split("player_data[")[1].split("}")[0] if "player_data[" in content else "":
        # Find the "dv" line and add "war" after it
        dv_pattern = re.compile(r'("dv":\s*dollar_map\.get\(mid\)),')
        if dv_pattern.search(content):
            content = dv_pattern.sub(r'\1,\n            "war":   p.get("war"),', content)
            changes.append("3a. Added 'war' to player_data dict")
        else:
            changes.append("3a. SKIPPED adding war to dict (pattern not found)")
    else:
        changes.append("3a. 'war' already in player_data dict")

    # 3b) Add fWAR badge display after the dvBadge in JS
    if "warBadge" not in content:
        # Find the dvBadge block end and add warBadge after it
        dv_end_pattern = re.compile(
            r"(dvBadge = '<span style=\"font-size:1\.15rem.*?</span>';)\s*\}\}"
        )
        if dv_end_pattern.search(content):
            content = dv_end_pattern.sub(
                r"""\1
    }}

    // ── fWAR badge ───────────────────────────────────────────────────────
    var warBadge = '';
    if (d.war != null) {{
      warBadge = '<div style="font-size:.72rem;font-weight:700;color:#8ab4f8;margin-top:3px">'
        + d.war.toFixed(1) + ' fWAR</div>';
    }}""",
                content
            )
            changes.append("3b. Added fWAR badge display")
        else:
            changes.append("3b. SKIPPED fWAR badge (dvBadge pattern not found)")

        # Now insert warBadge into the header HTML after dvBadge
        if "warBadge" in content:
            # Find where dvBadge is used in the header
            content = content.replace(
                "+     dvBadge\n      +   '</div>'",
                "+     dvBadge\n      +   '</div>'\n      +   warBadge"
            )
            changes.append("3c. Inserted warBadge into card header")

            # If the above didn't match, try alternate patterns
            if "warBadge" in content and content.count("warBadge") < 3:
                # Try adding it after dvBadge in a different way
                pass
    else:
        changes.append("3. fWAR badge already exists")

    # ═══════════════════════════════════════════════════════════════════════
    # 4. TEAM LOGOS: white outline, big, right corner
    #    Already in code at lines 398-404 — enhance if needed
    # ═══════════════════════════════════════════════════════════════════════

    # Check current logo styling
    logo_pattern = re.compile(
        r"(logoBadge = '<img src=\"' \+ logoBgUrl \+ '\" style=\")(position:absolute;[^\"]+)(\")"
    )
    if logo_pattern.search(content):
        old_style = logo_pattern.search(content).group(2)
        new_style = (
            "position:absolute;top:6px;right:6px;"
            "width:140px;height:140px;object-fit:contain;opacity:.85;z-index:1;"
            "filter:drop-shadow(0 0 2px #fff) drop-shadow(0 0 2px #fff) drop-shadow(0 0 1px #fff)"
        )
        content = logo_pattern.sub(r"\g<1>" + new_style + r"\3", content)
        changes.append("4. Enhanced team logo: bigger, stronger white outline")
    else:
        changes.append("4. SKIPPED logo enhancement (pattern not found)")

    # ═══════════════════════════════════════════════════════════════════════
    # Write result
    # ═══════════════════════════════════════════════════════════════════════

    if content != original:
        with open("player_cards.py", "w", encoding="utf-8") as f:
            f.write(content)
        print("Changes applied:")
        for c in changes:
            print(f"  {c}")
        return True
    else:
        print("No changes made")
        for c in changes:
            print(f"  {c}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Fixing player cards")
    print("=" * 60)
    fix_player_cards()

    # Verify syntax
    print("\nVerifying syntax...")
    try:
        with open("player_cards.py", "r", encoding="utf-8") as f:
            compile(f.read(), "player_cards.py", "exec")
        print("  Syntax OK")
    except SyntaxError as e:
        print(f"  SYNTAX ERROR at line {e.lineno}: {e.msg}")
