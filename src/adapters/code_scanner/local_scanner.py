"""로컬 디렉토리 코드 스캐너 — CodePort 구현."""
import ast
import re
from pathlib import Path

from src.core.ports.cache_port import RepoCachePort
from src.core.ports.code_port import CodePort, RepoRef

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".next"}
SKIP_EXTS = {".pyc", ".lock", ".log", ".env", ".DS_Store"}
MAX_FILES = 99999


class LocalCodeScanner(CodePort):
    """로컬 파일시스템 코드 스캔. 구현 코드 제외, 시그니처/구조만 추출."""

    def __init__(self, cache: RepoCachePort) -> None:
        self._cache = cache

    async def prepare(self, repo: RepoRef) -> None:
        pass  # 로컬은 준비 불필요

    async def scan(self, repo: RepoRef) -> dict:
        cache_key = f"local:{repo.source}"
        cached = await self._cache.get(cache_key)
        if cached:
            return cached

        path = Path(repo.source)
        if not path.exists():
            return self._empty(repo.name)

        file_tree = self._collect_tree(path)
        languages = self._detect_languages(file_tree)
        interfaces = self._extract_interfaces(path, file_tree)
        patterns = self._detect_patterns(file_tree, interfaces)

        result = {
            "name": repo.name,
            "file_tree": file_tree,
            "interfaces": interfaces,
            "patterns": patterns,
            "languages": languages,
        }
        await self._cache.set(cache_key, result)
        return result

    def _collect_tree(self, root: Path) -> list[str]:
        files = []
        for p in sorted(root.rglob("*")):
            if any(s in p.parts for s in SKIP_DIRS):
                continue
            if p.suffix in SKIP_EXTS or p.name.startswith("."):
                continue
            if p.is_file():
                files.append(str(p.relative_to(root)))
            if len(files) >= MAX_FILES:
                break
        return files

    def _detect_languages(self, tree: list[str]) -> list[str]:
        ext_map = {
            ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
            ".js": "JavaScript", ".jsx": "JavaScript", ".go": "Go",
            ".java": "Java", ".rs": "Rust", ".kt": "Kotlin",
        }
        found = set()
        for f in tree:
            ext = Path(f).suffix
            if ext in ext_map:
                found.add(ext_map[ext])
        return list(found)

    def _extract_interfaces(self, root: Path, tree: list[str]) -> list[dict]:
        interfaces = []
        py_files = [f for f in tree if f.endswith(".py")]
        ts_files = [f for f in tree if f.endswith((".ts", ".tsx")) and not f.endswith(".d.ts")]
        java_files = [f for f in tree if f.endswith((".java", ".kt"))]

        for rel in py_files:
            interfaces.extend(self._extract_python(root / rel, rel))
        for rel in ts_files:
            interfaces.extend(self._extract_typescript(root / rel, rel))
        for rel in java_files:
            interfaces.extend(self._extract_java(root / rel, rel))

        return interfaces

    def _extract_python(self, path: Path, rel: str) -> list[dict]:
        try:
            source = path.read_text(errors="ignore")
            tree = ast.parse(source)
        except Exception:
            return []

        results = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [ast.unparse(b) for b in node.bases]
                doc = ast.get_docstring(node) or ""
                results.append({
                    "file": rel,
                    "name": node.name,
                    "kind": "class",
                    "signature": f"class {node.name}({', '.join(bases)})",
                    "doc": doc,
                })
                for item in node.body:
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                        sig = self._py_func_sig(item)
                        results.append({
                            "file": rel,
                            "name": f"{node.name}.{item.name}",
                            "kind": "method",
                            "signature": sig,
                            "doc": ast.get_docstring(item) or "",
                        })
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if not any(
                    isinstance(p, ast.ClassDef) and node in ast.walk(p)
                    for p in ast.walk(tree)
                    if p is not node
                ):
                    results.append({
                        "file": rel,
                        "name": node.name,
                        "kind": "function",
                        "signature": self._py_func_sig(node),
                        "doc": ast.get_docstring(node) or "",
                    })
        return results

    def _py_func_sig(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        try:
            args = ast.unparse(node.args)
            ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
            prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            return f"{prefix}def {node.name}({args}){ret}"
        except Exception:
            return f"def {node.name}(...)"

    def _extract_typescript(self, path: Path, rel: str) -> list[dict]:
        try:
            source = path.read_text(errors="ignore")
        except Exception:
            return []

        results = []
        # 인터페이스/타입/클래스/함수 시그니처만 추출 (정규식)
        patterns = [
            (r"^export\s+(interface|type|class|abstract class)\s+(\w+)([^{]*)\{", "interface"),
            (r"^export\s+(async\s+)?function\s+(\w+)\s*\(([^)]*)\)\s*:\s*([\w<>|\[\]]+)", "function"),
            (r"^export\s+const\s+(\w+)\s*=\s*(async\s+)?\(([^)]*)\)\s*:\s*([\w<>|\[\]]+)", "const-fn"),
        ]
        for line in source.splitlines():
            line = line.strip()
            for pat, kind in patterns:
                m = re.match(pat, line)
                if m:
                    results.append({
                        "file": rel,
                        "name": m.group(2) if kind != "const-fn" else m.group(1),
                        "kind": kind,
                        "signature": line,
                        "doc": "",
                    })
        return results

    def _extract_java(self, path: Path, rel: str) -> list[dict]:
        try:
            source = path.read_text(errors="ignore")
        except Exception:
            return []

        results = []
        # 클래스/인터페이스/enum 선언
        class_pat = re.compile(
            r"(?:public\s+)?(?:(abstract|interface|enum)\s+)?(?:class|interface|enum)\s+(\w+)"
            r"(?:\s+extends\s+([\w<>, ]+?))?(?:\s+implements\s+([\w<>, ]+?))?\s*\{"
        )
        # 메서드 시그니처 (public/protected, 반환타입, 메서드명, 파라미터)
        method_pat = re.compile(
            r"^\s*(?:@\w+\s*(?:\([^)]*\)\s*)?)*"
            r"(?:public|protected)\s+(?:static\s+)?(?:final\s+)?"
            r"([\w<>\[\],? ]+?)\s+(\w+)\s*\(([^)]{0,200})\)\s*(?:throws\s+[\w, ]+)?\s*[{;]"
        )

        current_class: str | None = None
        for line in source.splitlines():
            cm = class_pat.search(line)
            if cm:
                kind = cm.group(1) or "class"
                name = cm.group(2)
                extends = (cm.group(3) or "").strip()
                implements = (cm.group(4) or "").strip()
                sig_parts = [f"{kind} {name}"]
                if extends:
                    sig_parts.append(f"extends {extends}")
                if implements:
                    sig_parts.append(f"implements {implements}")
                current_class = name
                results.append({
                    "file": rel,
                    "name": name,
                    "kind": kind,
                    "signature": " ".join(sig_parts),
                    "doc": "",
                })
                continue

            mm = method_pat.match(line)
            if mm and current_class:
                ret_type = mm.group(1).strip()
                method_name = mm.group(2)
                params = mm.group(3).strip()
                # 생성자 제외 (반환타입이 void/타입이 아닌 경우)
                if ret_type in ("public", "protected", "private", "static"):
                    continue
                results.append({
                    "file": rel,
                    "name": f"{current_class}.{method_name}",
                    "kind": "method",
                    "signature": f"{ret_type} {method_name}({params})",
                    "doc": "",
                })

        return results

    def _detect_patterns(self, tree: list[str], interfaces: list[dict]) -> list[str]:
        """실제 파일/인터페이스 존재 여부만으로 패턴 감지 — 추론/가정 없음."""
        patterns = []

        # 실제 Port 인터페이스 파일이 2개 이상 있을 때만 hexagonal
        port_files = [f for f in tree if re.search(r"[Pp]ort\.(java|kt|py|ts)$", f)]
        if len(port_files) >= 2:
            patterns.append(f"hexagonal({len(port_files)} port files)")

        # 실제 Mapper 파일 존재
        mapper_files = [f for f in tree if re.search(r"[Mm]apper\.(java|kt|xml)$", f)]
        if mapper_files:
            patterns.append(f"mybatis({len(mapper_files)} mappers)")

        # Controller/Service/Repository 레이어
        has_controller = any(re.search(r"[Cc]ontroller\.(java|kt|py|ts)$", f) for f in tree)
        has_service = any(re.search(r"[Ss]ervice\.(java|kt|py)$", f) for f in tree)
        has_repository = any(re.search(r"[Rr]epository\.(java|kt|py)$", f) for f in tree)
        if has_controller and has_service:
            patterns.append("layered(controller-service" + ("-repository" if has_repository else "") + ")")

        # JPA Entity
        entity_files = [f for f in tree if re.search(r"[Ee]ntity\.(java|kt)$", f)]
        if entity_files:
            patterns.append(f"jpa({len(entity_files)} entities)")

        # React/Vue/Next 프론트엔드
        if any(f.endswith((".tsx", ".jsx")) for f in tree):
            patterns.append("react")
        elif any(re.search(r"\.vue$", f) for f in tree):
            patterns.append("vue")

        # 실제 라우터 파일 (Next.js pages/app, express router)
        router_files = [f for f in tree if re.search(r"(pages|app)/.*\.(tsx?|jsx?)$", f) or re.search(r"router\.(ts|js)$", f)]
        if router_files:
            patterns.append("router-based")

        return patterns

    def _empty(self, name: str) -> dict:
        return {"name": name, "file_tree": [], "interfaces": [], "patterns": [], "languages": []}
