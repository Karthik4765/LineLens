"""Unit tests for the pure AST analyzer."""

from __future__ import annotations

import pytest

from app.services.analyzer import analyze_code


def line_facts(analysis: dict, number: int) -> dict:
    return analysis["lines"][number - 1]


def test_analyzes_a_loop_and_its_names():
    code = "for item in range(3):\n    total += item\n"

    analysis = analyze_code(code)

    assert analysis["max_loop_nesting"] == 1
    assert analysis["complexity_hint"] == "single loop, likely O(n)"
    assert line_facts(analysis, 1)["node_types"] == ["for", "call"]
    assert line_facts(analysis, 1)["names_defined"] == ["item"]
    assert line_facts(analysis, 1)["names_used"] == ["range"]
    assert line_facts(analysis, 2)["depth"] == 1
    assert line_facts(analysis, 2)["node_types"] == ["aug_assign"]
    assert "item" in line_facts(analysis, 2)["names_used"]


def test_detects_nested_loops_and_complexity():
    code = (
        "for row in grid:\n"
        "    for cell in row:\n"
        "        print(cell)\n"
    )

    analysis = analyze_code(code)

    assert analysis["max_loop_nesting"] == 2
    assert analysis["complexity_hint"] == "nested loops, likely O(n^2)"
    assert [line_facts(analysis, n)["depth"] for n in (1, 2, 3)] == [0, 1, 2]
    assert line_facts(analysis, 3)["node_types"] == ["call"]


def test_detects_functions_and_arguments():
    code = (
        "def add(left, right=1):\n"
        "    total = left + right\n"
        "    return total\n"
    )

    analysis = analyze_code(code)

    assert analysis["functions"] == [
        {"name": "add", "line": 1, "args": ["left", "right"], "async": False}
    ]
    assert line_facts(analysis, 1)["node_types"] == ["function_def"]
    assert line_facts(analysis, 1)["names_defined"] == ["left", "right", "add"]
    assert line_facts(analysis, 2)["node_types"] == ["assign"]
    assert line_facts(analysis, 2)["names_defined"] == ["total"]
    assert line_facts(analysis, 3)["node_types"] == ["return"]
    assert line_facts(analysis, 3)["names_used"] == ["total"]


def test_detects_classes_and_methods():
    code = (
        "class Point:\n"
        "    def __init__(self, x):\n"
        "        self.x = x\n"
    )

    analysis = analyze_code(code)

    assert analysis["classes"] == [{"name": "Point", "line": 1}]
    assert analysis["functions"][0]["name"] == "__init__"
    assert line_facts(analysis, 1)["names_defined"] == ["Point"]
    assert line_facts(analysis, 2)["depth"] == 1
    assert "self.x" in line_facts(analysis, 3)["names_defined"]


def test_detects_plain_and_from_imports():
    code = "import math\nfrom pathlib import Path as FilePath\n"

    analysis = analyze_code(code)

    assert analysis["imports"] == [
        {"module": "math", "name": "math", "kind": "import", "line": 1},
        {"module": "pathlib", "name": "FilePath", "kind": "from", "line": 2},
    ]
    assert line_facts(analysis, 1)["names_defined"] == ["math"]
    assert line_facts(analysis, 2)["names_defined"] == ["FilePath"]


def test_empty_source_has_no_facts():
    analysis = analyze_code("")

    assert analysis["total_lines"] == 0
    assert analysis["lines"] == []
    assert analysis["functions"] == []
    assert analysis["classes"] == []
    assert analysis["imports"] == []
    assert analysis["max_loop_nesting"] == 0


def test_blank_and_comment_lines_are_preserved_as_empty_facts():
    analysis = analyze_code("\n# a comment\nprint('hi')\n")

    assert analysis["total_lines"] == 3
    assert line_facts(analysis, 1)["node_types"] == []
    assert line_facts(analysis, 2)["node_types"] == []
    assert line_facts(analysis, 3)["node_types"] == ["call"]


def test_syntax_error_is_raised_with_line_number():
    with pytest.raises(SyntaxError) as error:
        analyze_code("def broken(:\n    pass\n")

    assert error.value.lineno == 1


def test_comprehensions_contribute_to_loop_nesting():
    analysis = analyze_code("pairs = [(x, y) for x in xs for y in ys]\n")

    assert analysis["max_loop_nesting"] == 2
    assert analysis["complexity_hint"] == "nested loops, likely O(n^2)"
    assert "pairs" in line_facts(analysis, 1)["names_defined"]
