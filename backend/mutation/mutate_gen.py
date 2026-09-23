"""AST-based mutant manifest generator for backend mutation testing.

Generates one-mutation-per-site mutants over the app package, writing a JSON
manifest consumed by mutate_run.py. Operators (classic mutmut/cosmic-ray set,
minus string-literal noise):

  * compare swaps: == <-> !=, < <-> <=, > <-> >=, is <-> is not,
    in <-> not in (each op of a chained comparison is its own site)
  * BoolOp swap: and <-> or
  * drop `not`
  * arithmetic swaps: + <-> -, * <-> /, // -> *, & <-> |
  * True <-> False
  * int off-by-one: n -> n+1 (and n -> n-1 when n > 0)
  * ternary branch swap: (a if c else b) -> (b if c else a)
  * break <-> continue

Excluded zones: annotations (arg/return/AnnAssign), decorator expressions.
Excluded files: everything outside app/, models/ (except helpers.py and
constants.py — pure SQLAlchemy declarative DDL is schema noise), __init__.py
re-exports. String constants are never mutated.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# module path fragments -> priority tier (1 = highest). Lower tier runs first.
TIER_1 = [
    "app/security.py",
    "app/permissions.py",
    "app/ratelimit.py",
    "app/deps.py",
    "app/api/auth.py",
    "app/api/_run_limits.py",
    "app/api/_shared.py",
    "app/services/",
]
TIER_2 = [
    "app/api/",
    "app/schemas/",
    "app/audit.py",
    "app/characters.py",
    "app/utils.py",
    "app/models/helpers.py",
    "app/models/constants.py",
    "app/simulation/",
]
TIER_3 = [  # everything else in app/ (seed, main, db, metrics, core, worker…)
    "app/",
]

COMPARE_SWAPS = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}

BINOP_SWAPS = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.FloorDiv: ast.Mult,
    ast.BitAnd: ast.BitOr,
    ast.BitOr: ast.BitAnd,
}

STMT_PARENTS = (
    ast.Module,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.If,
    ast.While,
    ast.For,
    ast.AsyncFor,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.TryStar,
    ast.ExceptHandler,
    ast.match_case,
)


@dataclass
class Site:
    kind: str  # compare/boolop/not/binop/boolconst/intconst/ifexp/loopjump
    detail: str  # human description of the mutation
    node_index: int  # index in the deterministic ast.walk order
    # extra data: for compare, which op position; for intconst, the delta
    op_pos: int = 0
    delta: int = 0
    meta: dict = field(default_factory=dict)


def module_tier(rel: str) -> int:
    for frag in TIER_1:
        if rel.startswith(frag) or rel == frag:
            return 1
    for frag in TIER_2:
        if rel.startswith(frag) or rel == frag:
            return 2
    for frag in TIER_3:
        if rel.startswith(frag) or rel == frag:
            return 3
    return 99


def target_files() -> list[Path]:
    out = []
    for p in sorted((BACKEND / "app").rglob("*.py")):
        rel = p.relative_to(BACKEND).as_posix()
        if rel.endswith("__init__.py"):
            continue
        if rel.startswith("app/models/") and rel not in (
            "app/models/helpers.py",
            "app/models/constants.py",
        ):
            continue
        out.append(p)
    return out


def build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def excluded_node_ids(tree: ast.AST) -> set[int]:
    """Node ids inside annotations and decorators — never mutate these."""
    bad: set[int] = set()

    def mark(sub: ast.AST | None) -> None:
        if sub is None:
            return
        for n in ast.walk(sub):
            bad.add(id(n))

    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            mark(node.annotation)
            continue
        if isinstance(node, (*STMT_PARENTS, ast.Lambda)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for dec in node.decorator_list:
                    mark(dec)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
                mark(node.returns)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                a = node.args
                for arg in (
                    a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]
                ):
                    if arg is not None:
                        mark(arg.annotation)
    return bad


def enclosing_statement(node: ast.AST, parents: dict[int, ast.AST]) -> ast.stmt | None:
    cur: ast.AST = node
    while True:
        if isinstance(cur, ast.stmt) and isinstance(parents.get(id(cur)), STMT_PARENTS):
            return cur
        nxt = parents.get(id(cur))
        if nxt is None:
            return None
        cur = nxt


def collect_sites(tree: ast.AST, bad: set[int]) -> list[Site]:
    sites: list[Site] = []
    for idx, node in enumerate(ast.walk(tree)):
        if id(node) in bad:
            continue
        if isinstance(node, ast.Compare):
            for pos, op in enumerate(node.ops):
                if type(op) in COMPARE_SWAPS:
                    sites.append(
                        Site(
                            kind="compare",
                            detail=f"{type(op).__name__} -> {COMPARE_SWAPS[type(op)].__name__}",
                            node_index=idx,
                            op_pos=pos,
                        )
                    )
        elif isinstance(node, ast.BoolOp):
            new = ast.Or if isinstance(node.op, ast.And) else ast.And
            sites.append(
                Site(
                    kind="boolop",
                    detail=f"{type(node.op).__name__} -> {new.__name__}",
                    node_index=idx,
                )
            )
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            sites.append(Site(kind="not", detail="drop `not`", node_index=idx))
        elif isinstance(node, ast.BinOp):
            if type(node.op) in BINOP_SWAPS:
                sites.append(
                    Site(
                        kind="binop",
                        detail=f"{type(node.op).__name__} -> {BINOP_SWAPS[type(node.op)].__name__}",
                        node_index=idx,
                    )
                )
        elif isinstance(node, ast.Constant):
            if type(node.value) is bool:
                sites.append(
                    Site(kind="boolconst", detail=f"{node.value} -> {not node.value}", node_index=idx)
                )
            elif type(node.value) is int:
                sites.append(Site(kind="intconst", detail="n -> n+1", node_index=idx, delta=1))
                if node.value > 0:
                    sites.append(
                        Site(kind="intconst", detail="n -> n-1", node_index=idx, delta=-1)
                    )
        elif isinstance(node, ast.IfExp):
            sites.append(Site(kind="ifexp", detail="swap branches", node_index=idx))
        elif isinstance(node, (ast.Break, ast.Continue)):
            tgt = "continue" if isinstance(node, ast.Break) else "break"
            src = "break" if isinstance(node, ast.Break) else "continue"
            sites.append(Site(kind="loopjump", detail=f"{src} -> {tgt}", node_index=idx))
    return sites


def nodes_in_walk_order(tree: ast.AST) -> list[ast.AST]:
    """Exactly ast.walk's order (BFS, deque) so site indices line up."""
    from collections import deque

    out: list[ast.AST] = []
    todo = deque([tree])
    while todo:
        n = todo.popleft()
        todo.extend(ast.iter_child_nodes(n))
        out.append(n)
    return out


def set_child(parent: ast.AST, node: ast.AST, new: ast.AST) -> bool:
    for fname, value in ast.iter_fields(parent):
        if isinstance(value, list):
            for i, item in enumerate(value):
                if item is node:
                    value[i] = new
                    return True
        elif value is node:
            setattr(parent, fname, new)
            return True
    return False


def apply_site(node: ast.AST, site: Site, parents: dict[int, ast.AST]) -> bool:
    if site.kind == "compare":
        if not isinstance(node, ast.Compare) or len(node.ops) <= site.op_pos:
            return False
        if type(node.ops[site.op_pos]) not in COMPARE_SWAPS:
            return False
        node.ops[site.op_pos] = COMPARE_SWAPS[type(node.ops[site.op_pos])]()
        return True
    if site.kind == "boolop":
        if not isinstance(node, ast.BoolOp):
            return False
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return True
    if site.kind == "not":
        if not isinstance(node, ast.UnaryOp):
            return False
        parent = parents.get(id(node))
        if parent is None:
            return False
        return set_child(parent, node, node.operand)
    if site.kind == "binop":
        if not isinstance(node, ast.BinOp) or type(node.op) not in BINOP_SWAPS:
            return False
        node.op = BINOP_SWAPS[type(node.op)]()
        return True
    if site.kind == "boolconst":
        if not isinstance(node, ast.Constant) or type(node.value) is not bool:
            return False
        node.value = not node.value
        return True
    if site.kind == "intconst":
        if not isinstance(node, ast.Constant) or type(node.value) is not int:
            return False
        node.value = node.value + site.delta
        return True
    if site.kind == "ifexp":
        if not isinstance(node, ast.IfExp):
            return False
        node.body, node.orelse = node.orelse, node.body
        return True
    return False


def splice(source: str, stmt: ast.stmt, new_text: str) -> str:
    lines = source.splitlines(keepends=True)
    s, e = stmt.lineno - 1, stmt.end_lineno  # exclusive
    replacement = " " * stmt.col_offset + new_text + "\n"
    return "".join(lines[:s]) + replacement + "".join(lines[e:])


def generate() -> None:
    manifest = []
    for path in target_files():
        rel = path.relative_to(BACKEND).as_posix()
        source = path.read_text()
        tree = ast.parse(source)
        bad = excluded_node_ids(tree)
        parents = build_parent_map(tree)
        sites = collect_sites(tree, bad)
        if not sites:
            continue
        all_nodes = nodes_in_walk_order(tree)
        for site in sites:
            node = all_nodes[site.node_index]
            if site.kind == "loopjump":
                # statement replacement is textual, no tree surgery needed
                stmt = node
                new_stmt_src = "continue" if isinstance(node, ast.Break) else "break"
            else:
                stmt = enclosing_statement(node, parents)
                if stmt is None:
                    continue
                local_nodes = nodes_in_walk_order(stmt)
                if not any(n is node for n in local_nodes):
                    continue
                li = [i for i, n in enumerate(local_nodes) if n is node][0]
                stmt_copy = copy.deepcopy(stmt)
                copy_local = nodes_in_walk_order(stmt_copy)
                target = copy_local[li]
                local_parents = build_parent_map(stmt_copy)
                if not apply_site(target, site, local_parents):
                    continue
                try:
                    new_stmt_src = ast.unparse(stmt_copy)
                except Exception:
                    continue
            if site.kind != "loopjump":
                # `break`/`continue` don't compile standalone; the full-file
                # compile below still validates them.
                try:
                    compile(new_stmt_src, "<mutant>", "exec")
                except Exception:
                    continue
            mutated_file = splice(source, stmt, new_stmt_src)
            try:
                compile(mutated_file, str(path), "exec")
            except SyntaxError:
                continue
            # scope: how deep is the statement (module/class = import-time)
            depth, probe = 0, parents.get(id(stmt))
            while probe is not None:
                if isinstance(probe, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    depth += 1
                probe = parents.get(id(probe))
            scope = "module" if depth == 0 else "function"
            mid = hashlib.sha1(
                f"{rel}:{stmt.lineno}:{site.kind}:{site.detail}:{ast.unparse(node) if not isinstance(node, ast.Constant) else repr(node.value)}".encode()
            ).hexdigest()[:12]
            manifest.append(
                {
                    "id": mid,
                    "file": rel,
                    "tier": module_tier(rel),
                    "scope": scope,
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                    "kind": site.kind,
                    "detail": site.detail,
                    "stmt_line": stmt.lineno,
                    "stmt_end_line": stmt.end_lineno,
                    "stmt_col": stmt.col_offset,
                    "orig_stmt": ast.unparse(stmt),
                    "mut_stmt": new_stmt_src,
                }
            )
    # Dedupe (id collisions from same-node sites) and write.
    seen: dict[str, dict] = {}
    for m in manifest:
        seen.setdefault(m["id"], m)
    out = BACKEND / "mutation" / "manifest.json"
    out.write_text(json.dumps(list(seen.values()), indent=1))
    from collections import Counter

    by_file = Counter(m["file"] for m in seen.values())
    by_kind = Counter(m["kind"] for m in seen.values())
    by_tier = Counter(m["tier"] for m in seen.values())
    print(f"total mutants: {len(seen)}  ->  {out}")
    print("by tier:", dict(sorted(by_tier.items())))
    print("by kind:", dict(by_kind.most_common()))
    print("top files:")
    for f, c in by_file.most_common(15):
        print(f"  {c:5d}  {f}")


if __name__ == "__main__":
    sys.exit(generate())
