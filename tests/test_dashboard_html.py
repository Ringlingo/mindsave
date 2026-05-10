"""Dashboard HTML structural validation"""
import re
import sys

with open(r"j:\workbuddy工作区\projects\mindsave\sdk\tools\mindsave_dashboard.html", encoding="utf-8") as f:
    html = f.read()

issues = []

script_open = len(re.findall(r"<script", html))
script_close = len(re.findall(r"</script>", html))
if script_open != script_close:
    issues.append(f"Unmatched script tags: {script_open} open, {script_close} close")

ids = re.findall(r'id="([^"]+)"', html)
dupes = [x for x in ids if ids.count(x) > 1]
if dupes:
    issues.append(f"Duplicate DOM IDs: {set(dupes)}")

file_refs = re.findall(r"file://[^\"]+", html)
if file_refs:
    issues.append(f"file:// references found: {file_refs}")

cdn_refs = re.findall(r"cdn\.jsdelivr|unpkg\.com|cdnjs", html)
if cdn_refs:
    issues.append(f"CDN dependencies: {cdn_refs}")

if "const rLarge = 0" in html:
    print("[PASS] rLarge = 0 (semicircle fix confirmed)")

if issues:
    for i in issues:
        print(f"[FAIL] {i}")
    sys.exit(1)
else:
    print("[PASS] Dashboard HTML: no structural issues found")
    print(f"  - Script tags: {script_open} open / {script_close} close")
    print(f"  - DOM IDs: {len(set(ids))} unique")
    print(f"  - No file:// references")
    print(f"  - No CDN dependencies")
