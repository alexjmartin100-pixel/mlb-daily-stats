#!/usr/bin/env python3
"""Show _pw_fetch_json function and step 6/6 code"""
import os

FILENAME = "fetch_mlb_stats.py"
filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), FILENAME)

with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Show _pw_fetch_json function (starts at line 382)
print("=" * 60)
print("_pw_fetch_json function:")
print("=" * 60)
start = None
for i, l in enumerate(lines):
    if 'def _pw_fetch_json' in l:
        start = i
        break
if start:
    for j in range(start, min(start + 130, len(lines))):
        print(f"{j+1}: {lines[j]}", end='')
        # Stop when we hit the next top-level def
        if j > start + 5 and lines[j].startswith('def '):
            break

# Show step 6/6 code (around line 5179)
print("\n" + "=" * 60)
print("Step 6/6 code (Season leaderboards):")
print("=" * 60)
for i, l in enumerate(lines):
    if '6/6' in l and 'Season' in l:
        start = i
        for j in range(max(0, start-2), min(start + 80, len(lines))):
            print(f"{j+1}: {lines[j]}", end='')
        break

# Show code around line 1290-1350 (batting leaderboard call)
print("\n" + "=" * 60)
print("Batting leaderboard call (around line 1290-1360):")
print("=" * 60)
for j in range(1288, min(1360, len(lines))):
    print(f"{j+1}: {lines[j]}", end='')
