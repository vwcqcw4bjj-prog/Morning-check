import json, re

with open("headline_pipeline_strict_all.ipynb", encoding="utf-8") as f:
    nb = json.load(f)
cells = [c for c in nb["cells"] if c.get("cell_type") == "code"]

header = "# pipeline.py\n# from pipeline import run, build_market_df\n\n"
all_code = [header]
for cell in cells:
    src = "".join(cell.get("source", []))
    if not src.strip() or src.strip() == "display(df_market)":
        continue
    src = re.sub(r"^display\(.*?\)\s*$", "", src, flags=re.MULTILINE)
    if src.strip():
        all_code.append(src)

full = "\n\n".join(all_code)

pairs = [
    ("\u201c", '"'), ("\u201d", '"'),
    ("\u2018", "'"), ("\u2019", "'"),
    ("\u2014", "-"), ("\u2013", "-"),
]
for bad, good in pairs:
    full = full.replace(bad, good)

with open("pipeline.py", "w", encoding="utf-8") as f:
    f.write(full)
print("done:", len(full.splitlines()), "lines")
