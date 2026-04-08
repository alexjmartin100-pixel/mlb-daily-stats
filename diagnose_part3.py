#!/usr/bin/env python3
"""Show daily.yml workflow file"""
import os
filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "workflows", "daily.yml")
if os.path.exists(filepath):
    print(open(filepath).read())
else:
    print(f"NOT FOUND: {filepath}")
