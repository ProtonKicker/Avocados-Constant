from __future__ import annotations

import re
from dataclasses import dataclass


class MathSyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class TranslatedLine:
    mode: str
    code: str
    assigned_names: tuple[str, ...] = ()


_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*(.+)$")
_WORD_ALIASES = {
    "arcsin": "asin",
    "arccos": "acos",
    "arctan": "atan",
}


def translate_math_line(line: str) -> TranslatedLine:
    assign_match = _ASSIGN_RE.match(line)
    if assign_match and "==" not in line:
        name = assign_match.group(1)
        expr = normalize_math_expression(assign_match.group(2))
        return TranslatedLine("exec", f"{name} = {expr}", (name,))
    return TranslatedLine("eval", normalize_math_expression(line))


def normalize_math_expression(expr: str) -> str:
    text = expr.strip()
    if not text:
        raise MathSyntaxError("Empty expression")

    text = _replace_matrix_literals(text)
    text = _replace_word_aliases(text)
    text = _replace_exponents(text)
    range_expr = _translate_range_expression(text)
    if range_expr is not None:
        return range_expr
    return text


def _replace_exponents(expr: str) -> str:
    return expr.replace("^", "**")


def _replace_word_aliases(expr: str) -> str:
    for source, target in _WORD_ALIASES.items():
        expr = re.sub(rf"\b{source}\b", target, expr)
    return expr


def _translate_range_expression(expr: str) -> str | None:
    parts = _split_top_level(expr, ":")
    if len(parts) == 2:
        return f"matlab_range({normalize_math_expression(parts[0])}, {normalize_math_expression(parts[1])})"
    if len(parts) == 3:
        return (
            "matlab_range("
            f"{normalize_math_expression(parts[0])}, "
            f"{normalize_math_expression(parts[2])}, "
            f"{normalize_math_expression(parts[1])})"
        )
    return None


def _replace_matrix_literals(expr: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch != "[":
            out.append(ch)
            i += 1
            continue

        prev = _previous_non_space(expr, i)
        if prev is not None and (prev.isalnum() or prev in "_)]"):
            out.append(ch)
            i += 1
            continue

        end = _find_matching(expr, i, "[", "]")
        if end == -1:
            raise MathSyntaxError("Unclosed matrix literal")
        content = expr[i + 1 : end]
        replacement = _translate_matrix_literal(content)
        if replacement is None:
            out.append(expr[i : end + 1])
        else:
            out.append(replacement)
        i = end + 1
    return "".join(out)


def _translate_matrix_literal(content: str) -> str | None:
    rows = _split_top_level(content, ";")
    has_row_break = len(rows) > 1
    normalized_rows: list[list[str]] = []
    whitespace_matrix = False
    for row in rows:
        cells = _split_row_cells(row)
        if len(cells) > 1 and "," not in row:
            whitespace_matrix = True
        normalized_rows.append(cells)

    if not has_row_break and not whitespace_matrix:
        return None

    rendered_rows: list[str] = []
    for cells in normalized_rows:
        rendered_rows.append(
            "[" + ", ".join(normalize_math_expression(cell) for cell in cells) + "]"
        )
    return "np.array([" + ", ".join(rendered_rows) + "])"


def _split_row_cells(row: str) -> list[str]:
    values: list[str] = []
    token: list[str] = []
    depth = 0
    i = 0
    saw_space_separator = False
    while i < len(row):
        ch = row[i]
        if ch in "([{":
            depth += 1
            token.append(ch)
            i += 1
            continue
        if ch in ")]}":
            depth -= 1
            token.append(ch)
            i += 1
            continue
        if depth == 0 and ch == ",":
            if token or values:
                values.append("".join(token).strip())
                token = []
            i += 1
            continue
        if depth == 0 and ch.isspace():
            j = i
            while j < len(row) and row[j].isspace():
                j += 1
            if token and j < len(row):
                values.append("".join(token).strip())
                token = []
                saw_space_separator = True
            i = j
            continue
        token.append(ch)
        i += 1

    if token:
        values.append("".join(token).strip())
    values = [value for value in values if value]
    if not values:
        raise MathSyntaxError("Empty matrix row")
    if not saw_space_separator and len(values) == 1 and "," not in row:
        return values
    return values


def _split_top_level(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    token: list[str] = []
    depth = 0
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == separator and depth == 0:
            parts.append("".join(token).strip())
            token = []
            continue
        token.append(ch)
    if depth != 0:
        raise MathSyntaxError("Mismatched brackets")
    if not parts:
        return [text]
    parts.append("".join(token).strip())
    return parts


def _find_matching(text: str, start: int, left: str, right: str) -> int:
    depth = 0
    for index in range(start, len(text)):
        ch = text[index]
        if ch == left:
            depth += 1
        elif ch == right:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _previous_non_space(text: str, index: int) -> str | None:
    for pos in range(index - 1, -1, -1):
        if not text[pos].isspace():
            return text[pos]
    return None
