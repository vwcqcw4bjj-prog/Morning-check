import re

with open("pipeline.py", encoding="utf-8") as f:
    src = f.read()

# 全角文字を半角に置換
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

# 空の関数定義にpassを追加
def fix_empty_functions(code):
    lines = code.split("\n")
    result = []
    for i, line in enumerate(lines):
        result.append(line)
        stripped = line.rstrip()
        if stripped.endswith(":"):
            is_def = re.match(r"^(\s*)(def |class |if |else:|elif |for |while |try:|except|finally:|with )", stripped)
            if is_def:
                indent = len(line) - len(line.lstrip())
                next_idx = i + 1
                while next_idx < len(lines) and lines[next_idx].strip() == "":
                    next_idx += 1
                if next_idx >= len(lines):
                    result.append(" " * (indent + 4) + "pass")
                else:
                    next_indent = len(lines[next_idx]) - len(lines[next_idx].lstrip())
                    if next_indent <= indent and lines[next_idx].strip() != "":
                        result.append(" " * (indent + 4) + "pass")
    return "\n".join(result)

src = fix_empty_functions(src)

with open("pipeline.py", "w", encoding="utf-8") as f:
    f.write(src)

print("done:", len(src.splitlines()), "lines")
