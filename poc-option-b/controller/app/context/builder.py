"""Code context for the thinking roles: repository map, symbols and the most relevant files.

Everything is read from the project mirror at the run's base commit. The bundle
fits a character budget so that it stays inside the model's context window.
"""

from dataclasses import asdict, dataclass, field
import hashlib
import json
import posixpath
import re

from controller.app.projects.repo import Mirror

SYMBOL_PATTERNS = {
    (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"): [
        re.compile(r"^export\s+(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)", re.M),
        re.compile(r"^export\s+(?:const|let|var|class)\s+([A-Za-z_$][\w$]*)", re.M),
        re.compile(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", re.M),
        re.compile(r"^class\s+([A-Za-z_$][\w$]*)", re.M),
        re.compile(r"^const\s+([A-Za-z_$][\w$]*)\s*=", re.M),
    ],
    (".py",): [
        re.compile(r"^(?:async\s+)?def\s+([A-Za-z_]\w*)", re.M),
        re.compile(r"^class\s+([A-Za-z_]\w*)", re.M),
        re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=", re.M),
    ],
    (".java", ".kt", ".cs"): [
        re.compile(r"\b(?:class|interface|enum|record)\s+([A-Z]\w*)"),
        re.compile(r"^\s+(?:public|protected)\s+(?:static\s+)?[\w<>\[\], ]+\s+([a-z]\w*)\s*\(", re.M),
    ],
    (".go",): [re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.M),
               re.compile(r"^type\s+([A-Za-z_]\w*)", re.M)],
}
IMPORT = re.compile(r"""(?:from|import)\s+['"](\.{1,2}/[^'"]+)['"]""")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{3,}")
STOP = frozenset("""this that with from have will should each when then they them their there which while
about into only also must when user users task tasks able want shows show make sure using used other""".split())
TEXT_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py", ".java", ".kt", ".cs", ".go", ".rb", ".php",
                 ".html", ".css", ".scss", ".json", ".md", ".yaml", ".yml", ".toml", ".sql", ".sh")


def symbols_of(path: str, text: str) -> list[str]:
    for suffixes, patterns in SYMBOL_PATTERNS.items():
        if path.endswith(suffixes):
            names: list[str] = []
            for pattern in patterns:
                names += [name for name in pattern.findall(text) if name not in names]
            return names
    return []


def keywords(*texts: str) -> set[str]:
    words = set()
    for text in texts:
        for word in WORD.findall(text or ""):
            lower = word.lower()
            if lower not in STOP:
                words.add(lower)
    return words


@dataclass
class ContextBundle:
    files: list[str]
    symbols: dict[str, list[str]]
    selected_files: list[dict] = field(default_factory=list)  # path, content, truncated
    budget: int = 0
    used: int = 0

    def as_input(self) -> dict:
        return {"files": self.files, "symbols": self.symbols, "selected_files": self.selected_files}

    def manifest(self) -> dict:
        """What the role saw, without file contents; stored as an artifact for reproducibility."""
        digest = hashlib.sha256(json.dumps(self.as_input(), sort_keys=True).encode()).hexdigest()
        return {"sha256": digest, "budget": self.budget, "used": self.used, "file_count": len(self.files),
                "selected": [{"path": f["path"], "truncated": f["truncated"]} for f in self.selected_files]}

    def known_symbols(self) -> dict[str, set[str]]:
        return {path: set(names) for path, names in self.symbols.items()}


def build_context(mirror: Mirror, commit: str, files: list[str], query_texts: list[str], budget: int,
                  always: list[str] | None = None) -> ContextBundle:
    """Pick files by relevance to the query texts, add their local imports and tests, fill the budget."""
    contents: dict[str, str] = {}
    for path in files:
        if path.endswith(TEXT_SUFFIXES):
            text = mirror.read_file(commit, path)
            if text is not None and "\x00" not in text:
                contents[path] = text
    symbols = {path: names for path, text in contents.items() if (names := symbols_of(path, text))}
    words = keywords(*query_texts)
    mentioned = {path for path in contents if any(path in text for text in query_texts)}

    def score(path: str) -> float:
        text = contents[path].lower()
        hits = sum(min(text.count(word), 5) for word in words)
        path_hits = sum(3 for word in words if word in path.lower())
        symbol_hits = sum(2 for name in symbols.get(path, []) if name.lower() in words)
        return hits + path_hits + symbol_hits + (50 if path in mentioned else 0) + (100 if path in (always or []) else 0)

    ranked = sorted((p for p in contents if score(p) > 0), key=lambda p: (-score(p), p))
    order: list[str] = []
    for path in ranked:
        if path not in order:
            order.append(path)
        for target in IMPORT.findall(contents[path]):
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
            if resolved in contents and resolved not in order:
                order.append(resolved)
        base = posixpath.splitext(posixpath.basename(path))[0]
        for candidate in contents:
            if candidate not in order and ("test" in candidate.lower()) and base in contents[candidate]:
                order.append(candidate)

    bundle = ContextBundle(files=list(files), symbols=symbols, budget=budget)
    bundle.used = len(json.dumps({"files": files, "symbols": symbols}))
    for path in order:
        text = contents[path]
        room = budget - bundle.used
        if room < 400:
            break
        truncated = len(text) > room
        if truncated:
            text = text[: room - 40] + "\n/* … truncated by the context budget … */\n"
        bundle.selected_files.append({"path": path, "content": text, "truncated": truncated})
        bundle.used += len(text) + len(path) + 40
    return bundle


def bundle_dict(bundle: ContextBundle) -> dict:
    return asdict(bundle)
