"""Configuration keys as symbols: YAML, JSON, TOML, INI/properties and .env files.

Dotted paths (`jobs.build.steps`, `scripts.test`, `services.db.image`, `tool.ruff.line-length`) with their
line numbers, so agents can find and fetch one setting instead of reading a whole config file.
Depth is capped (default 3) so lockfiles and huge generated JSON don't flood the index.
"""

from __future__ import annotations

import json
import re

MAX_KEYS = 400
YAML_KEY_RX = re.compile(r"^([ \t]*)(?:-[ \t]+)?([A-Za-z0-9_][\w.\-/]*|\"[^\"]+\"|'[^']+')[ \t]*:(?:[ \t]|$)")
TOML_TABLE_RX = re.compile(r"^\s*\[\[?([^\]]+)\]\]?\s*$")
TOML_KEY_RX = re.compile(r"^\s*([A-Za-z0-9_\-\"'.]+)\s*=")
INI_SECTION_RX = re.compile(r"^\s*\[([^\]]+)\]\s*$")
INI_KEY_RX = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*[=:]")
ENV_KEY_RX = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def config_language(path: str, lang: str) -> str | None:
    name = path.rsplit("/", 1)[-1].lower()
    if name.endswith((".lock", "-lock.json", ".lock.json")) or name in ("package-lock.json", "composer.lock"):
        return None
    if lang in ("yaml", "json", "toml"):
        return lang
    if name.endswith((".ini", ".cfg", ".properties", ".conf")) or name in ("setup.cfg", "tox.ini", ".editorconfig"):
        return "ini"
    if name.startswith(".env") or name.endswith(".env"):
        return "env"
    if name.endswith(".jsonc") or name in ("tsconfig.json",):
        return "json"
    return None


def extract_keys(path: str, lang: str, source: str, max_depth: int = 3) -> list[tuple[str, int, str]]:
    """Returns [(dotted_key, line, value_preview)]."""
    kind = config_language(path, lang)
    if not kind or len(source) > 400_000:
        return []
    lines = source.split("\n")
    out: list[tuple[str, int, str]] = []

    def add(key: str, line: int, value: str):
        if len(out) < MAX_KEYS:
            out.append((key, line, value.strip()[:120]))

    if kind == "yaml":
        stack: list[tuple[int, str]] = []
        in_block = None
        for i, ln in enumerate(lines, 1):
            if not ln.strip() or ln.lstrip().startswith("#") or ln.strip() in ("---", "..."):
                continue
            ind = len(ln) - len(ln.lstrip())
            if in_block is not None:
                if ind > in_block:
                    continue
                in_block = None
            m = YAML_KEY_RX.match(ln)
            if not m:
                continue
            key = m.group(2).strip("\"'")
            depth_ind = len(m.group(1)) + (2 if ln.lstrip().startswith("- ") else 0)
            while stack and stack[-1][0] >= depth_ind:
                stack.pop()
            stack.append((depth_ind, key))
            if len(stack) <= max_depth:
                rest = ln[m.end():]
                add(".".join(k for _, k in stack), i, rest)
            if ln.rstrip().endswith(("|", ">", "|-", ">-")):
                in_block = ind
    elif kind == "json":
        try:
            data = json.loads(re.sub(r"(?m)^\s*//.*$", "", source))
        except (json.JSONDecodeError, ValueError):
            return []
        pos = {"i": 0}

        def line_of(key: str) -> int:
            idx = source.find(f'"{key}"', pos["i"])
            if idx < 0:
                idx = source.find(f'"{key}"')
            if idx >= 0:
                pos["i"] = idx + 1
                return source.count("\n", 0, idx) + 1
            return 1

        def walk(obj, prefix: str, depth: int):
            if not isinstance(obj, dict) or depth > max_depth:
                return
            for k, v in obj.items():
                key = f"{prefix}.{k}" if prefix else str(k)
                add(key, line_of(str(k)), json.dumps(v)[:120] if not isinstance(v, (dict, list)) else "")
                walk(v, key, depth + 1)
        walk(data, "", 1)
    elif kind == "toml":
        table = ""
        for i, ln in enumerate(lines, 1):
            t = TOML_TABLE_RX.match(ln)
            if t:
                table = t.group(1).strip().replace('"', "")
                if table.count(".") < max_depth:
                    add(table, i, "")
                continue
            k = TOML_KEY_RX.match(ln)
            if k and not ln.lstrip().startswith("#"):
                key = f"{table}.{k.group(1).strip(chr(34) + chr(39))}" if table else k.group(1).strip("\"'")
                if key.count(".") < max_depth:
                    add(key, i, ln.split("=", 1)[1])
    elif kind == "ini":
        section = ""
        for i, ln in enumerate(lines, 1):
            s = INI_SECTION_RX.match(ln)
            if s:
                section = s.group(1).strip()
                add(section, i, "")
                continue
            k = INI_KEY_RX.match(ln)
            if k and not ln.lstrip().startswith(("#", ";")):
                add(f"{section}.{k.group(1)}" if section else k.group(1), i, re.split(r"[=:]", ln, 1)[1])
    elif kind == "env":
        for i, ln in enumerate(lines, 1):
            k = ENV_KEY_RX.match(ln)
            if k:
                add(k.group(1), i, "")  # never keep values from env files
    return out


# ----------------------------------------------------------------------------- documentation sections

MAX_SECTIONS = 300
MD_HEADING_RX = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
RST_UNDERLINE_RX = re.compile(r"^([=\-~^\"'`#*+])\1{2,}\s*$")


def doc_language(path: str, lang: str) -> str | None:
    name = path.lower()
    if lang == "markdown" or name.endswith((".md", ".mdx", ".markdown")):
        return "markdown"
    if name.endswith((".rst", ".adoc", ".asciidoc")):
        return "rst" if name.endswith(".rst") else "adoc"
    return None


def extract_sections(path: str, lang: str, source: str) -> list[tuple[str, str, int, int, int]]:
    """Headings as navigable sections: [(title, breadcrumb, level, start_line, end_line)].

    end_line runs to the line before the next heading of the same or higher level, so fetching a section
    returns just that part of the document. Fenced code blocks are skipped (their `#` lines are comments).
    """
    kind = doc_language(path, lang)
    if not kind or len(source) > 1_000_000:
        return []
    lines = source.split("\n")
    heads: list[tuple[int, str, int]] = []  # (level, title, line)
    fence = None
    rst_levels: list[str] = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if kind == "markdown":
            if s.startswith(("```", "~~~")):
                mark = s[:3]
                fence = None if fence == mark else (fence or mark)
                continue
            if fence:
                continue
            m = MD_HEADING_RX.match(ln)
            if m:
                heads.append((len(m.group(1)), m.group(2).strip(), i + 1))
        elif kind == "adoc":
            m = re.match(r"^(={1,6})\s+(.+)$", ln)
            if m:
                heads.append((len(m.group(1)), m.group(2).strip(), i + 1))
        elif i > 0 and RST_UNDERLINE_RX.match(ln) and lines[i - 1].strip() and len(s) >= len(lines[i - 1].strip()) - 1 \
                and not RST_UNDERLINE_RX.match(lines[i - 1]):
            ch = s[0]
            if ch not in rst_levels:
                rst_levels.append(ch)
            heads.append((rst_levels.index(ch) + 1, lines[i - 1].strip(), i))
        if len(heads) >= MAX_SECTIONS:
            break
    out, stack = [], []
    for k, (level, title, line) in enumerate(heads):
        title = re.sub(r"\s*\{#[^}]*\}\s*$", "", re.sub(r"[*_`]|\[([^\]]*)\]\([^)]*\)", r"\1", title)).strip()[:160]
        if not title:
            continue
        end = len(lines)
        for lv2, _, ln2 in heads[k + 1:]:
            if lv2 <= level:
                end = ln2 - 1
                break
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        out.append((title, " > ".join(t for _, t in stack), level, line, max(line, end)))
    return out
