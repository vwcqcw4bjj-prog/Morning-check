src = open("pipeline.py", encoding="utf-8").read()
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
open("pipeline.py", "w", encoding="utf-8").write(src)
print("done")
