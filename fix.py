import json, re

with open("headline_pipeline_strict_all.ipynb", encoding="utf-8") as f:
    nb = json.load(f)

cells = [c for c in nb["cells"] if c.get("cell_type") == "code"]

header = "# pipeline.py\n# from pipeline import run, build_market_df\n\n"
all_code = [header]

for cell in cells:
    src = "".join(cell.get("source", []))
    if not src.strip():
        continue
    src = re.sub(r"^display\(.*?\)\s*$", "", src, flags=re.MULTILINE)
    if not src.strip():
        continue
    all_code.append(src)

full = "\n\n".join(all_code)

pairs = [
    ("\u201c", '"'), ("\u201d", '"'),
    ("\u2018", "'"), ("\u2019", "'"),
    ("\u2014", "-"), ("\u2013", "-"),
]
for bad, good in pairs:
    full = full.replace(bad, good)

lines = full.split("\n")
safe_lines = []
skip = False
for line in lines:
    stripped = line.strip()
    if stripped.startswith("df_all") or stripped.startswith("df_week") or stripped.startswith("one_week_ago") or stripped.startswith("send_email(") or stripped.startswith("sections ="):
        skip = True
    if skip and (stripped == "" or stripped.startswith("#")):
        skip = False
        continue
    if not skip:
        safe_lines.append(line)

result = "\n".join(safe_lines)

with open("pipeline.py", "w", encoding="utf-8") as f:
    f.write(result)

print("done:", len(result.splitlines()), "lines")
