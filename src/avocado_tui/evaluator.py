from __future__ import annotations

import ast
import contextlib
import io
from dataclasses import dataclass
from typing import Any

try:
    import numpy as np
except ImportError:  # pragma: no cover - exercised when numpy is absent at runtime
    np = None

from .latex_syntax import looks_like_latex_syntax, translate_latex_line
from .math_engine import MathSyntaxError, evaluate_math_line


@dataclass(frozen=True)
class EvalOutput:
    lines: list[str]
    namespace: dict[str, Any]


def _squash_stream_text(text: str) -> str:
    return text.rstrip("\r\n").replace("\r\n", "\n").replace("\n", "\\n")


def _combine_stream_and_value(*parts: str) -> str:
    return " | ".join(p for p in parts if p)



def _assigned_names(stmt: ast.stmt) -> list[str]:
    if isinstance(stmt, ast.Assign):
        names: list[str] = []
        for t in stmt.targets:
            if isinstance(t, ast.Name):
                names.append(t.id)
        return names
    if isinstance(stmt, ast.AnnAssign):
        if isinstance(stmt.target, ast.Name):
            return [stmt.target.id]
        return []
    if isinstance(stmt, ast.AugAssign):
        if isinstance(stmt.target, ast.Name):
            return [stmt.target.id]
        return []
    return []


_PYTHON_STATEMENT_PREFIXES = (
    "import ",
    "from ",
    "for ",
    "async for ",
    "if ",
    "elif ",
    "while ",
    "with ",
    "async with ",
    "try",
    "except",
    "finally:",
    "else:",
    "def ",
    "async def ",
    "class ",
    "return",
    "pass",
    "raise ",
    "assert ",
    "del ",
    "global ",
    "nonlocal ",
    "yield ",
    "match ",
    "case ",
)

_PYTHON_BLOCK_CONTINUATIONS = ("elif ", "else:", "except", "finally:")


def _is_python_statement_candidate(line: str) -> bool:
    stripped = line.lstrip()
    return any(stripped.startswith(prefix) for prefix in _PYTHON_STATEMENT_PREFIXES)


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_block_continuation(line: str) -> bool:
    stripped = line.lstrip()
    return any(stripped.startswith(prefix) for prefix in _PYTHON_BLOCK_CONTINUATIONS)


def _collect_python_chunk(lines: list[str], start: int) -> tuple[list[str], int] | None:
    first_line = lines[start]
    if not _is_python_statement_candidate(first_line):
        return None

    stripped = first_line.lstrip()
    if not stripped.endswith(":"):
        return [first_line], start + 1

    chunk = [first_line]
    base_indent = _indent_width(first_line)
    saw_body = False
    index = start + 1
    while index < len(lines):
        candidate = lines[index]
        stripped_candidate = candidate.strip()
        if not stripped_candidate:
            chunk.append(candidate)
            index += 1
            continue

        indent = _indent_width(candidate)
        if indent > base_indent:
            saw_body = True
            chunk.append(candidate)
            index += 1
            continue
        if indent == base_indent and _is_block_continuation(candidate):
            chunk.append(candidate)
            index += 1
            continue
        break

    if not saw_body:
        return [first_line], start + 1
    return chunk, index


def _normalize_display_value(value: Any) -> Any:
    if np is None:
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray) and value.ndim == 0:
        return value.item()
    return value


def _format_value(value: Any) -> str:
    return repr(_normalize_display_value(value)).replace("\n", "\\n")


def _evaluate_python_chunk(
    source: str, ns: dict[str, Any], stdout_io: io.StringIO, stderr_io: io.StringIO
) -> str:
    mod = ast.parse(source, mode="exec")
    if len(mod.body) != 1:
        code = compile(mod, "<avocado>", "exec")
        exec(code, ns, ns)
        return _combine_stream_and_value(
            _squash_stream_text(stdout_io.getvalue()),
            _squash_stream_text(stderr_io.getvalue()),
        )

    stmt = mod.body[0]
    if isinstance(stmt, ast.Expr):
        expr_code = compile(ast.Expression(stmt.value), "<avocado>", "eval")
        value = eval(expr_code, ns, ns)

        stdout_text = _squash_stream_text(stdout_io.getvalue())
        stderr_text = _squash_stream_text(stderr_io.getvalue())
        value_text = _format_value(value)
        if value is None and stdout_text:
            return _combine_stream_and_value(stdout_text, stderr_text)
        return _combine_stream_and_value(stdout_text, stderr_text, value_text)

    assigned = _assigned_names(stmt)
    code = compile(mod, "<avocado>", "exec")
    exec(code, ns, ns)

    stdout_text = _squash_stream_text(stdout_io.getvalue())
    stderr_text = _squash_stream_text(stderr_io.getvalue())
    if len(assigned) == 1 and assigned[0] in ns:
        return _combine_stream_and_value(
            stdout_text, stderr_text, _format_value(ns[assigned[0]])
        )
    return _combine_stream_and_value(stdout_text, stderr_text)


def _append_chunk_output(outputs: list[str], chunk_len: int, rendered: str) -> None:
    if chunk_len > 1:
        outputs.extend([""] * (chunk_len - 1))
    outputs.append(rendered)


def _evaluate_math_line_with_fallback(line: str, ns: dict[str, Any]):
    if looks_like_latex_syntax(line):
        try:
            return evaluate_math_line(translate_latex_line(line), ns)
        except (MathSyntaxError, SyntaxError):
            pass
    return evaluate_math_line(line, ns)


def evaluate_source_linewise(source: str, *, stop_on_error: bool = True) -> EvalOutput:
    ns: dict[str, Any] = {}
    outputs: list[str] = []

    error_seen = False
    lines = source.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if error_seen and stop_on_error:
            outputs.append("Skipped")
            index += 1
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            outputs.append("")
            index += 1
            continue

        python_chunk = _collect_python_chunk(lines, index)
        if python_chunk is not None:
            chunk_lines, next_index = python_chunk
            stdout_io = io.StringIO()
            stderr_io = io.StringIO()
            try:
                with contextlib.redirect_stdout(stdout_io), contextlib.redirect_stderr(
                    stderr_io
                ):
                    rendered = _evaluate_python_chunk(
                        "\n".join(chunk_lines), ns, stdout_io, stderr_io
                    )
                _append_chunk_output(outputs, len(chunk_lines), rendered)
            except Exception as e:
                stdout_text = _squash_stream_text(stdout_io.getvalue())
                stderr_text = _squash_stream_text(stderr_io.getvalue())
                err_text = _squash_stream_text(f"{type(e).__name__}: {e}")
                _append_chunk_output(
                    outputs,
                    len(chunk_lines),
                    _combine_stream_and_value(stdout_text, stderr_text, err_text),
                )
                error_seen = True
            index = next_index
            continue

        stdout_io = io.StringIO()
        stderr_io = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout_io), contextlib.redirect_stderr(
                stderr_io
            ):
                if not _is_python_statement_candidate(line):
                    try:
                        math_result = _evaluate_math_line_with_fallback(line, ns)
                    except (MathSyntaxError, SyntaxError):
                        outputs.append(
                            _evaluate_python_chunk(line, ns, stdout_io, stderr_io)
                        )
                    else:
                        stdout_text = _squash_stream_text(stdout_io.getvalue())
                        stderr_text = _squash_stream_text(stderr_io.getvalue())
                        if len(math_result.assigned_names) == 1:
                            assigned_name = math_result.assigned_names[0]
                            outputs.append(
                                _combine_stream_and_value(
                                    stdout_text,
                                    stderr_text,
                                    _format_value(ns[assigned_name]),
                                )
                            )
                        elif math_result.value is None and stdout_text:
                            outputs.append(
                                _combine_stream_and_value(stdout_text, stderr_text)
                            )
                        else:
                            outputs.append(
                                _combine_stream_and_value(
                                    stdout_text,
                                    stderr_text,
                                    _format_value(math_result.value),
                                )
                            )
                        index += 1
                        continue
                else:
                    outputs.append(_evaluate_python_chunk(line, ns, stdout_io, stderr_io))
                    index += 1
                    continue
        except Exception as e:
            stdout_text = _squash_stream_text(stdout_io.getvalue())
            stderr_text = _squash_stream_text(stderr_io.getvalue())
            err_text = _squash_stream_text(f"{type(e).__name__}: {e}")
            outputs.append(_combine_stream_and_value(stdout_text, stderr_text, err_text))
            error_seen = True
        index += 1

    return EvalOutput(lines=outputs, namespace=ns)


def truncate_lines(lines: list[str], width: int) -> list[str]:
    if width <= 0:
        return ["" for _ in lines]

    out: list[str] = []
    for s in lines:
        if len(s) <= width:
            out.append(s)
            continue
        if width <= 1:
            out.append("…")
            continue
        out.append(s[: width - 1] + "…")
    return out
