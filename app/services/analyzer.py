"""Pure AST analysis for Python source code.

The analyzer turns a syntax tree into compact, JSON-friendly facts that are sent
to the language model. It also powers the complexity hint in the UI. It performs
no I/O and has no dependency on FastAPI, the database, or any AI provider.
"""

from __future__ import annotations

import ast
from typing import Any

_NODE_TYPES: dict[type[ast.AST], str] = {
    ast.FunctionDef: "function_def",
    ast.AsyncFunctionDef: "function_def",
    ast.ClassDef: "class_def",
    ast.For: "for",
    ast.AsyncFor: "for",
    ast.While: "while",
    ast.If: "if",
    ast.Assign: "assign",
    ast.AnnAssign: "assign",
    ast.AugAssign: "aug_assign",
    ast.NamedExpr: "assign",
    ast.Call: "call",
    ast.Return: "return",
    ast.Import: "import",
    ast.ImportFrom: "import",
    ast.Try: "try",
    ast.ExceptHandler: "except",
    ast.Raise: "raise",
    ast.With: "with",
    ast.AsyncWith: "with",
    ast.Lambda: "lambda",
    ast.Yield: "yield",
    ast.YieldFrom: "yield",
    ast.Assert: "assert",
    ast.Delete: "delete",
    ast.Global: "global",
    ast.Nonlocal: "nonlocal",
    ast.Await: "await",
    ast.Match: "match",
    ast.ListComp: "comprehension",
    ast.SetComp: "comprehension",
    ast.DictComp: "comprehension",
    ast.GeneratorExp: "comprehension",
    ast.Break: "break",
    ast.Continue: "continue",
    ast.Pass: "pass",
}

def _argument_names(arguments: ast.arguments) -> list[str]:
    names = [arg.arg for arg in arguments.posonlyargs]
    names.extend(arg.arg for arg in arguments.args)
    if arguments.vararg:
        names.append(arguments.vararg.arg)
    names.extend(arg.arg for arg in arguments.kwonlyargs)
    if arguments.kwarg:
        names.append(arguments.kwarg.arg)
    return names


def _attribute_name(node: ast.Attribute | ast.Subscript) -> str | None:
    if isinstance(node, ast.Attribute):
        prefix = _attribute_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Subscript):
        return _attribute_name(node.value)
    if isinstance(node, ast.Name):
        return node.id
    return None


def _target_names(node: ast.AST | None) -> list[str]:
    """Collect identifiers bound by an assignment-like target."""

    if node is None:
        return []
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for item in node.elts:
            names.extend(_target_names(item))
        return names
    if isinstance(node, ast.Starred):
        return _target_names(node.value)
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        name = _attribute_name(node)
        return [name] if name else []
    return []


class _Analyzer:
    def __init__(self, source: str) -> None:
        self.source_line_count = len(source.splitlines())
        self.lines: dict[int, dict[str, Any]] = {
            line: {
                "line": line,
                "node_types": [],
                "depth": 0,
                "names_defined": [],
                "names_used": [],
            }
            for line in range(1, self.source_line_count + 1)
        }
        self.functions: list[dict[str, Any]] = []
        self.classes: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.max_loop_nesting = 0

    def analyze(self, tree: ast.Module) -> dict[str, Any]:
        self._walk(tree, depth=0, loop_depth=0)
        return {
            "total_lines": self.source_line_count,
            "functions": self.functions,
            "classes": self.classes,
            "imports": self.imports,
            "max_loop_nesting": self.max_loop_nesting,
            "complexity_hint": _complexity_hint(self.max_loop_nesting),
            "lines": [self.lines[number] for number in sorted(self.lines)],
        }

    def _facts_for(self, node: ast.AST) -> dict[str, Any] | None:
        line = getattr(node, "lineno", None)
        if not isinstance(line, int) or line not in self.lines:
            return None
        return self.lines[line]

    def _record_node(self, node: ast.AST, depth: int) -> None:
        node_type = _NODE_TYPES.get(type(node))
        facts = self._facts_for(node)
        if facts is None:
            return
        facts["depth"] = max(facts["depth"], depth)
        if node_type and node_type not in facts["node_types"]:
            facts["node_types"].append(node_type)

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            self._add_name(node, "names_used", node.func.attr)

    def _add_name(self, node: ast.AST, key: str, name: str) -> None:
        facts = self._facts_for(node)
        if facts is not None and name and name not in facts[key]:
            facts[key].append(name)

    def _record_arguments(self, node: ast.AST, arguments: ast.arguments) -> None:
        for name in _argument_names(arguments):
            self._add_name(node, "names_defined", name)

    def _record_import(self, node: ast.Import) -> None:
        for alias in node.names:
            binding = alias.asname or alias.name.split(".")[0]
            self._add_name(node, "names_defined", binding)
            self.imports.append(
                {
                    "module": alias.name,
                    "name": binding,
                    "kind": "import",
                    "line": node.lineno,
                }
            )

    def _record_import_from(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        for alias in node.names:
            binding = alias.asname or alias.name
            self._add_name(node, "names_defined", binding)
            self.imports.append(
                {
                    "module": module,
                    "name": binding,
                    "kind": "from",
                    "line": node.lineno,
                }
            )

    def _walk(self, node: ast.AST | None, depth: int, loop_depth: int) -> None:
        if node is None:
            return

        self._record_node(node, depth)

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._record_arguments(node, node.args)
            self.functions.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "args": _argument_names(node.args),
                    "async": isinstance(node, ast.AsyncFunctionDef),
                }
            )
            self._add_name(node, "names_defined", node.name)
            for decorator in node.decorator_list:
                self._walk(decorator, depth, loop_depth)
            self._walk(node.returns, depth, loop_depth)
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                self._walk(default, depth, loop_depth)
            for statement in node.body:
                self._walk(statement, depth + 1, loop_depth=0)
            return

        if isinstance(node, ast.ClassDef):
            self.classes.append({"name": node.name, "line": node.lineno})
            self._add_name(node, "names_defined", node.name)
            for decorator in node.decorator_list:
                self._walk(decorator, depth, loop_depth)
            for base in node.bases:
                self._walk(base, depth, loop_depth)
            for keyword in node.keywords:
                self._walk(keyword, depth, loop_depth)
            for statement in node.body:
                self._walk(statement, depth + 1, loop_depth=0)
            return

        if isinstance(node, (ast.For, ast.AsyncFor)):
            self.max_loop_nesting = max(self.max_loop_nesting, loop_depth + 1)
            for name in _target_names(node.target):
                self._add_name(node, "names_defined", name)
            self._walk(node.target, depth, loop_depth)
            self._walk(node.iter, depth, loop_depth)
            for statement in node.body:
                self._walk(statement, depth + 1, loop_depth + 1)
            for statement in node.orelse:
                self._walk(statement, depth + 1, loop_depth)
            return

        if isinstance(node, ast.While):
            self.max_loop_nesting = max(self.max_loop_nesting, loop_depth + 1)
            self._walk(node.test, depth, loop_depth)
            for statement in node.body:
                self._walk(statement, depth + 1, loop_depth + 1)
            for statement in node.orelse:
                self._walk(statement, depth + 1, loop_depth)
            return

        if isinstance(node, ast.If):
            self._walk(node.test, depth, loop_depth)
            for statement in node.body:
                self._walk(statement, depth + 1, loop_depth)
            for statement in node.orelse:
                self._walk(statement, depth + 1, loop_depth)
            return

        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                self._walk(item.context_expr, depth, loop_depth)
                for name in _target_names(item.optional_vars):
                    self._add_name(node, "names_defined", name)
                self._walk(item.optional_vars, depth, loop_depth)
            for statement in node.body:
                self._walk(statement, depth + 1, loop_depth)
            return

        if isinstance(node, ast.Try):
            for statement in node.body:
                self._walk(statement, depth + 1, loop_depth)
            for handler in node.handlers:
                self._walk(handler, depth + 1, loop_depth)
            for statement in node.orelse:
                self._walk(statement, depth + 1, loop_depth)
            for statement in node.finalbody:
                self._walk(statement, depth + 1, loop_depth)
            return

        if isinstance(node, ast.ExceptHandler):
            if node.name:
                self._add_name(node, "names_defined", node.name)
            self._walk(node.type, depth, loop_depth)
            for statement in node.body:
                self._walk(statement, depth + 1, loop_depth)
            return

        if isinstance(node, ast.Match):
            self._walk(node.subject, depth, loop_depth)
            for case in node.cases:
                self._walk(case, depth + 1, loop_depth)
            return

        if isinstance(node, ast.match_case):
            self._walk(node.pattern, depth, loop_depth)
            self._walk(node.guard, depth, loop_depth)
            for statement in node.body:
                self._walk(statement, depth + 1, loop_depth)
            return

        if isinstance(
            node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            self.max_loop_nesting = max(
                self.max_loop_nesting, loop_depth + len(node.generators)
            )
            for generator in node.generators:
                for name in _target_names(generator.target):
                    self._add_name(node, "names_defined", name)
                self._walk(generator.target, depth, loop_depth)
                self._walk(generator.iter, depth, loop_depth)
                for condition in generator.ifs:
                    self._walk(condition, depth, loop_depth)
            if isinstance(node, ast.DictComp):
                self._walk(node.key, depth + 1, loop_depth)
                self._walk(node.value, depth + 1, loop_depth)
            else:
                self._walk(node.elt, depth + 1, loop_depth)
            return

        if isinstance(node, ast.Lambda):
            self._record_arguments(node, node.args)
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                self._walk(default, depth, loop_depth)
            self._walk(node.body, depth + 1, loop_depth=0)
            return

        if isinstance(node, ast.Import):
            self._record_import(node)
            return

        if isinstance(node, ast.ImportFrom):
            self._record_import_from(node)
            return

        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in _target_names(target):
                    self._add_name(node, "names_defined", name)

        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                self._add_name(node, "names_used", node.id)
            elif isinstance(node.ctx, (ast.Store, ast.Del)):
                self._add_name(node, "names_defined", node.id)

        for child in ast.iter_child_nodes(node):
            self._walk(child, depth, loop_depth)


def _complexity_hint(max_loop_nesting: int) -> str:
    if max_loop_nesting >= 3:
        return "deeply nested loops, likely O(n^3) or worse"
    if max_loop_nesting == 2:
        return "nested loops, likely O(n^2)"
    if max_loop_nesting == 1:
        return "single loop, likely O(n)"
    return "no loops, likely O(1) for straight-line code"


def analyze_code(code: str) -> dict[str, Any]:
    """Parse Python source and return per-line and program-level AST facts.

    Raises SyntaxError when the source is not valid Python.
    """

    tree = ast.parse(code, filename="<linelens>")
    return _Analyzer(code).analyze(tree)
