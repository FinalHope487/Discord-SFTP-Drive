"""Print where each block sits in a source or Markdown file, so a reader can open one block.

usage (repo root): python tools/outline.py FILE [FILE...] [--min N]

One line per block:
    path:start-end  kind name  (N 行)
`start-end` are the line numbers to pass to a reader as offset and limit, so a 1000-line
file costs one outline plus the one block that matters. `--min N` hides entries shorter
than N lines.

| type | blocks |
|---|---|
| .py | top-level functions and classes, methods nested (ast: exact) |
| .ts .tsx .js .jsx .mjs .cjs | top-level function/class/const/interface/type/enum, class methods nested |
| .c .h .cpp .cc .cxx .hpp .hh | function definitions, struct/class/union/enum, namespace contents nested |
| .css | rules, @media/@supports/@layer contents nested |
| .md | headings; a section runs to the next heading of the same or a higher level |

Everything but .py is read with regular expressions over brace depth, with comments and
strings blanked first: unusual layouts can be missed, never mis-numbered. Other types print
the line count and exit 0.
"""
import argparse
import ast
import re
import sys
from pathlib import Path

JS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
C = {".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh"}
CSS = {".css"}
MD = {".md"}
OPEN, CLOSE = "{([", "})]"
# a line ending, or the next line starting, with one of these continues the statement
CONTINUES = ("=", "|", "&", ",", "+", "-", "*", "?", ":", ".", "=>", "(")


# ---------- Python -------------------------------------------------------------

def py_entries(text, path):
    """Yield (depth, kind, name, start, end) for functions and classes, methods nested."""
    def walk(body, depth):
        for node in body:
            if isinstance(node, ast.ClassDef):
                yield depth, "class", node.name, node.lineno, node.end_lineno
                yield from walk(node.body, depth + 1)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                yield depth, kind, node.name, node.lineno, node.end_lineno

    yield from walk(ast.parse(text, filename=str(path)).body, 0)


# ---------- Markdown -----------------------------------------------------------

def md_entries(text):
    lines = text.split("\n")
    heads, fence = [], None
    for i, line in enumerate(lines):
        mark = re.match(r"\s{0,3}(```|~~~)", line)
        if mark:
            fence = None if fence == mark.group(1) else (fence or mark.group(1))
            continue
        head = re.match(r"(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if head and fence is None:
            heads.append((i, len(head.group(1)), head.group(2)))
    last = len(lines) - (1 if text.endswith("\n") else 0)
    top = min((level for _, level, _ in heads), default=1)
    for n, (i, level, title) in enumerate(heads):
        end = next((j for j, lv, _ in heads[n + 1:] if lv <= level), last)
        yield level - top, "#" * level, title, i + 1, end


# ---------- brace languages ----------------------------------------------------

def blank(text, line_comment, template):
    """Comments and string contents become spaces; newlines stay, so line numbers hold.

    ' and " strings end at the line end: a stray apostrophe in JSX text costs one line."""
    out, i, n = list(text), 0, len(text)
    while i < n:
        c = text[i]
        if line_comment and text.startswith("//", i):
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
        elif text.startswith("/*", i):
            while i < n and not text.startswith("*/", i):
                out[i] = out[i] if text[i] == "\n" else " "
                i += 1
            out[i:i + 2] = "  "
            i += 2
        elif c in "'\"" or (template and c == "`"):
            i += 1
            while i < n and text[i] != c and (c == "`" or text[i] != "\n"):
                if text[i] == "\\":
                    out[i] = " "
                    i += 1
                if i < n and text[i] != "\n":
                    out[i] = " "
                i += 1
            i += 1
        else:
            i += 1
    return "".join(out)


class Braces:
    def __init__(self, code):
        self.code = code
        self.lines = code.split("\n")
        self.start = []  # nesting depth before each line
        depth = 0
        for line in self.lines:
            self.start.append(depth)
            for ch in line:
                if ch in OPEN:
                    depth += 1
                elif ch in CLOSE:
                    depth = max(depth - 1, 0)
        self.start.append(depth)

    def end(self, i, depth, stop):
        """Last line of the item starting at line i: back to `depth` after opening, or a `;`."""
        opened = False
        for j in range(i, stop):
            opened = opened or "{" in self.lines[j]
            if self.start[j + 1] != depth:
                continue
            line = self.lines[j].rstrip()
            if opened or line.endswith(";"):
                return j
            if line.endswith(CONTINUES):  # no-semicolon style: the statement goes on
                continue
            after = next((k for k in range(j + 1, stop) if self.lines[k].strip()), None)
            if after is None or not self.lines[after].lstrip().startswith(CONTINUES + ("{",)):
                return j
        return None

    def opens_before_semicolon(self, i, depth):
        """Whether the statement at line i reaches a `{` before a `;` at its own depth."""
        level = depth
        for ch in "\n".join(self.lines[i:i + 20]):
            if level == depth and ch == "{":
                return True
            if level == depth and ch == ";":
                return False
            level += (ch in OPEN) - (ch in CLOSE)
            if level < depth:
                return False
        return False


JS_TOP = re.compile(
    r"(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
    r"(function\*?|class|const|let|var|interface|type|enum|namespace)\s+([\w$]+)")
JS_METHOD = re.compile(
    r"\s+(?:(?:public|private|protected|static|async|readonly|override|get|set)\s+)*"
    r"\*?(#?[\w$]+)\s*(?:<[^>]*>)?\s*\(")
JS_CALL = re.compile(r"\s*(describe|test|it|suite|bench)((?:\.\w+)*)\s*\(\s*['\"`](.*?)['\"`]")
C_TYPE = re.compile(r"(?:typedef\s+)?(?:template\s*<.*>\s*)?(struct|class|union|enum(?:\s+class)?|namespace)\s+([\w:]+)?")
C_FUNC = re.compile(r"[\w:~<>,*&\s]*?([~\w]+(?:::[~\w]+)*|operator\S+)\s*\(")
KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "sizeof", "do", "else", "new", "delete"}


def brace_entries(text, suffix):
    js, css = suffix in JS, suffix in CSS
    b = Braces(blank(text, line_comment=not css, template=js))
    total = len(b.lines) - (1 if text.endswith("\n") else 0)
    original = text.split("\n")

    def match(i, depth, member):
        code = b.lines[i]
        if not code.strip() or code.lstrip().startswith(("#", "}", ")", "]")):
            return None
        if js:
            call = JS_CALL.match(original[i])
            if call and member != "class":
                kind = "describe" if ".describe" in call.group(2) else call.group(1)  # test.describe(
                return kind, call.group(3), kind in ("describe", "suite")
            if member == "calls":
                return None
            hit = (JS_METHOD if member else JS_TOP).match(code if member else code.lstrip())
            if not hit:
                return None
            if member:
                return ("method", hit.group(1), False) if hit.group(1) not in KEYWORDS else None
            kind = hit.group(1)
            return kind, hit.group(2), kind in ("class", "namespace")
        if css:
            if not b.opens_before_semicolon(i, depth):
                return None
            k = next(k for k in range(i, len(b.lines)) if "{" in b.lines[k])
            name = " ".join(line.strip() for line in original[i:k + 1]).split("{")[0].strip()
            if len(name) > 60:
                name = name[:57] + "..."
            at = name.startswith(("@media", "@supports", "@layer", "@container"))
            return (name.split()[0] if at else "rule"), (name[len(name.split()[0]):].strip() if at else name), at
        hit = C_TYPE.match(code.lstrip())
        if hit and b.opens_before_semicolon(i, depth):
            kind = hit.group(1).split()[0]
            return kind, hit.group(2) or "(anonymous)", kind in ("namespace", "struct", "class")
        if code.lstrip().startswith('extern "C"') or re.match(r'\s*extern\s+"', original[i]):
            return ("extern", '"C"', True) if b.opens_before_semicolon(i, depth) else None
        hit = C_FUNC.match(code)
        if hit and hit.group(1) not in KEYWORDS and b.opens_before_semicolon(i, depth):
            return "function", hit.group(1), False
        return None

    def scan(lo, hi, depth, member, indent):
        i = lo
        while i < hi:
            if b.start[i] != depth:
                i += 1
                continue
            hit = match(i, depth, member)
            if not hit:
                i += 1
                continue
            kind, name, container = hit
            end = b.end(i, depth, hi)
            if end is None:
                i += 1
                continue
            yield indent, kind, name, i + 1, min(end + 1, total)
            if container:
                if js:
                    inner = "class" if kind == "class" else "calls" if kind in ("describe", "suite") else None
                else:
                    inner = "class" if not css and kind in ("struct", "class") else None
                # the body's own depth: one `{` for a class, `(` plus `{` for describe('x', () => {
                yield from scan(i + 1, end, b.start[i + 1], inner, indent + 1)
            i = end + 1

    yield from scan(0, len(b.lines) - 1, 0, False, 0)


# ---------- output -------------------------------------------------------------

def entries_for(path):
    text = Path(path).read_text(encoding="utf-8")
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return py_entries(text, path)
    if suffix in MD:
        return md_entries(text)
    if suffix in JS | C | CSS:
        return brace_entries(text, suffix)
    return None


def outline(path, minimum=0):
    found = entries_for(path)
    if found is None:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        count = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        return [f"{path}: 不支援的類型（{Path(path).suffix or '無副檔名'}），共 {count} 行"]
    lines = []
    for depth, kind, name, start, end in found:
        length = end - start + 1
        if length >= minimum:
            lines.append(f"{path}:{start}-{end}  {'  ' * depth}{kind} {name}  ({length} 行)")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="+")
    parser.add_argument("--min", type=int, default=0, dest="minimum")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    for path in args.files:
        print("\n".join(outline(path, args.minimum)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
