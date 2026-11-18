"""Complete generic selections and equal, preselected pytest time budgets."""

from __future__ import annotations

import ast
import hashlib
import math
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

SELECTION_POLICY = "all-collected-statement-coverers-prioritized-v2"
TIMEOUT_POLICY = "concrete-selection-size-v1"


def collected_nodes(provenance: Mapping[str, Any]) -> tuple[str, ...]:
    """Read the concrete inventory from the same passing coverage publication."""
    inventory = provenance.get("collection_inventory")
    if (
        not isinstance(inventory, dict)
        or type(inventory.get("exit_code")) is not int
        or inventory["exit_code"] != 0
    ):
        raise ValueError("complete test inventory missing; regenerate passing mutation coverage")
    items = inventory.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("complete test inventory is empty; regenerate passing mutation coverage")
    nodes: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("malformed collected test item")
        node, file = item.get("nodeid"), item.get("file")
        if not isinstance(node, str) or not isinstance(file, str):
            raise ValueError("collected test item lacks concrete node/file identifiers")
        path = PurePosixPath(file)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not file.startswith("tests/")
            or not node.startswith(file + "::")
        ):
            raise ValueError("collected test item is not a concrete test beneath tests/")
        nodes.append(node)
    if len(set(nodes)) != len(nodes):
        raise ValueError("complete test inventory contains duplicate concrete nodes")
    return tuple(nodes)


def ancestors(node: str) -> set[str]:
    """Exactly match a file, class, function, parameter family, or concrete case."""
    parts = node.split("[", 1)[0].split("::")
    return {node, *("::".join(parts[:index]) for index in range(1, len(parts) + 1))}


class ConcreteInventory:
    def __init__(self, provenance: Mapping[str, Any]):
        self.nodes = collected_nodes(provenance)
        self.by_selector: dict[str, set[str]] = {}
        for node in self.nodes:
            for selector in ancestors(node):
                self.by_selector.setdefault(selector, set()).add(node)

    def expand(self, selectors: Sequence[str]) -> set[str]:
        nodes: set[str] = set()
        for selector in selectors:
            matches = self.by_selector.get(selector)
            if not matches:
                raise ValueError(f"selector absent from complete captured inventory: {selector}")
            nodes.update(matches)
        if not nodes:
            raise ValueError("empty mutation test selection")
        return nodes


class GenericSelection:
    """A module edit can affect readers that never execute its import-time line."""

    def __init__(
        self,
        contexts: Mapping[str, Mapping[int, set[str]]],
        provenance: Mapping[str, Any],
        *,
        backend: Path,
    ):
        self.contexts = contexts
        self.provenance = provenance
        self.backend = backend
        self._inventory: ConcreteInventory | None = None
        self._imports: dict[str, set[str]] | None = None
        self._statement_spans: dict[str, tuple[int, set[tuple[int, int]]]] = {}

    @staticmethod
    def configuration() -> dict[str, str]:
        return {
            "policy": SELECTION_POLICY,
            "function": "every-measured-replaced-statement-coverer",
            "function_span": "validated-ast-statement-bounds",
            "module": "entire-passing-collected-inventory",
            "order": "line-coverers,direct-importers,file-coverers,remaining-collection-order",
        }

    def inventory(self) -> ConcreteInventory:
        if self._inventory is None:
            self._inventory = ConcreteInventory(self.provenance)
        return self._inventory

    def importers(self, source_file: str) -> set[str]:
        if self._imports is None:
            index: dict[str, set[str]] = {}
            expected_tests = self.provenance.get("inputs", {}).get("tests", {})
            test_files = {node.split("::", 1)[0] for node in self.inventory().nodes}
            for file in sorted(test_files):
                source = (self.backend / file).read_bytes()
                if hashlib.sha256(source).hexdigest() != expected_tests.get(file):
                    raise ValueError(f"test importer source changed after coverage: {file}")
                for node in ast.walk(ast.parse(source, filename=file)):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        names = [
                            node.module,
                            *(node.module + "." + alias.name for alias in node.names),
                        ]
                    for name in names:
                        index.setdefault(name, set()).add(file)
            self._imports = index
        module = source_file.removesuffix(".py").replace("/", ".")
        if module.endswith(".__init__"):
            module = module.removesuffix(".__init__")
        return self._imports.get(module, set())

    def statement_bounds(self, mutant: Mapping[str, Any]) -> tuple[int, int]:
        """Use only an actual captured statement, never neighboring function code."""
        start, end, line = (mutant.get(key) for key in ("stmt_line", "stmt_end_line", "line"))
        if (
            type(start) is not int
            or type(end) is not int
            or type(line) is not int
            or not 1 <= start <= line <= end
        ):
            raise ValueError("mutation lacks valid replaced-statement bounds")
        file = mutant["file"]
        if file not in self._statement_spans:
            inputs = self.provenance.get("inputs", {})
            expected = inputs.get("sources", {}).get(file) or inputs.get("support", {}).get(file)
            if not isinstance(expected, str):
                raise ValueError(f"statement source is absent from captured inputs: {file}")
            try:
                source = (self.backend / file).read_bytes()
                if hashlib.sha256(source).hexdigest() != expected:
                    raise ValueError(f"statement source changed after coverage: {file}")
                tree = ast.parse(source, filename=file)
            except (OSError, SyntaxError, UnicodeError) as exc:
                raise ValueError(f"captured statement source cannot be read: {file}") from exc
            spans: set[tuple[int, int]] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.stmt) and node.end_lineno is not None:
                    first = node.lineno
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        first = min([first, *(item.lineno for item in node.decorator_list)])
                    spans.add((first, node.end_lineno))
            self._statement_spans[file] = (len(source.splitlines()), spans)
        length, spans = self._statement_spans[file]
        if end > length or (start, end) not in spans:
            raise ValueError("replaced-statement bounds do not match captured source")
        return start, end

    def select(self, mutant: Mapping[str, Any]) -> list[str]:
        lines = self.contexts.get(mutant["file"], {})
        if mutant.get("scope") != "module":
            start, end = self.statement_bounds(mutant)
            # An AST expression's line can be a non-executable parenthesis or
            # a short-circuited subexpression. Every coverer of the statement
            # being replaced is relevant, including its other branches.
            measured = sorted(
                {node for line in range(start, end + 1) for node in lines.get(line, ())}
            )
            if not measured:
                return []
            inventory = self.inventory()
            expanded = inventory.expand(measured)
            return [node for node in inventory.nodes if node in expanded]
        inventory = self.inventory()
        measured = sorted(lines.get(mutant["line"], set()))
        line_nodes = inventory.expand(measured) if measured else set()
        importer_files = self.importers(mutant["file"])
        file_selectors = sorted({node for nodes in lines.values() for node in nodes})
        file_nodes = inventory.expand(file_selectors) if file_selectors else set()

        def priority(node: str) -> int:
            if node in line_nodes:
                return 0
            if node.split("::", 1)[0] in importer_files:
                return 1
            return 2 if node in file_nodes else 3

        # Stable sort preserves the passing baseline's concrete collection order
        # within each tier. Every collected case still runs for survival.
        return sorted(inventory.nodes, key=priority)


class SelectionTimeoutPolicy:
    """Budget is fixed from captured collection before clean/mutated execution."""

    def __init__(
        self,
        provenance: Mapping[str, Any],
        *,
        explicit_seconds: float | None = None,
    ):
        if explicit_seconds is not None and (
            isinstance(explicit_seconds, bool)
            or not math.isfinite(explicit_seconds)
            or explicit_seconds <= 0
        ):
            raise ValueError("mutation timeout must be a finite positive number")
        self.explicit_seconds = explicit_seconds
        self.provenance = provenance
        self._inventory: ConcreteInventory | None = None

    def configuration(self) -> dict[str, str | float | None]:
        return {
            "policy": TIMEOUT_POLICY,
            "count": "unique-concrete-collected-nodes",
            "minimum_seconds": 300.0,
            "startup_seconds": 120.0,
            "seconds_per_test": 2.0,
            "explicit_seconds": self.explicit_seconds,
        }

    def seconds(self, selectors: Sequence[str]) -> float:
        if self.explicit_seconds is not None:
            return self.explicit_seconds
        if self._inventory is None:
            self._inventory = ConcreteInventory(self.provenance)
        count = len(self._inventory.expand(selectors))
        return max(300.0, 120.0 + 2.0 * count)
