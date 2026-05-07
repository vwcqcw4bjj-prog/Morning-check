with open("pipeline.py", encoding="utf-8") as f:
    src = f.read()

pairs = [
    ("\u201c", '"'),
    ("\u201d", '"'),
    ("\u2018", "'"),
    ("\u2019", "'"),
    ("\u2014", "-"),
    ("\u2013", "-"),
]
for bad, good in pairs:
    src = src.replace(bad, good)

with open("pipeline.py", "w", encoding="utf-8") as f:
    f.write(src)

print("done:", len(src.splitlines()), "lines")
