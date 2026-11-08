from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Input, Static, TextArea

from .config import (
    MAX_RESULTS_WIDTH,
    MIN_RESULTS_WIDTH,
    load_results_width,
    save_results_width,
)
from .evaluator import evaluate_source_linewise, truncate_lines
from .prelude import build_prelude_source, prelude_line_count
from .themes import (
    LIGHT,
    DARK,
    detect_scheme,
    palette_for,
    theme_name_for,
)


TOOLBAR_LABEL = "ctrl+q quit  ctrl+n new  ctrl+s save  ctrl+r results"
MIN_WINDOW_WIDTH = cell_len(f" {TOOLBAR_LABEL} ")
MIN_LEFT_PANEL_WIDTH = (MIN_WINDOW_WIDTH + 1) // 2
TEXTAREA_FRAME_WIDTH = 2
MIN_EDITOR_WIDGET_WIDTH = MIN_LEFT_PANEL_WIDTH + TEXTAREA_FRAME_WIDTH
MIN_WINDOW_HEIGHT = 7
MIN_CONTENT_HEIGHT = 5


class PathPromptScreen(ModalScreen[Optional[str]]):
    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    PathPromptScreen {
        align: left top;
        padding: 1 1 0 1;
    }

    #path-dialog {
        width: 100%;
        padding: 1 2;
        background: transparent;
    }

    #path-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    #path-help {
        color: $muted;
        margin-top: 1;
        width: 100%;
        content-align: right middle;
    }

    #path-submit {
        color: $muted;
        margin-top: 1;
    }

    #path-cancel {
        color: $muted;
    }

    #path {
        width: 100%;
        background: $canvas;
        color: $ink;
        border: tall $border-blurred;
    }

    #path:focus {
        border: tall $border;
    }
    """

    def __init__(
        self,
        initial_path: str = "",
        title: str = "Enter file path",
        placeholder: str = "Enter file path…",
        path_help_text: str = "",
        submit_text: str = "Enter=confirm",
        cancel_text: str = "Esc=cancel",
    ) -> None:
        super().__init__()
        self._initial_path = initial_path
        self._title = title
        self._placeholder = placeholder
        self._path_help_text = path_help_text
        self._submit_text = submit_text
        self._cancel_text = cancel_text

    def compose(self) -> ComposeResult:
        with Vertical(id="path-dialog"):
            yield Static(self._title, id="path-title")
            yield Input(value=self._initial_path, placeholder=self._placeholder, id="path")
            yield Static(self._path_help_text, id="path-help")
            yield Static(self._submit_text, id="path-submit")
            yield Static(self._cancel_text, id="path-cancel")

    def on_mount(self) -> None:
        input_widget = self.query_one(Input)
        input_widget.focus()
        input_widget.cursor_position = len(input_widget.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value if value else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SyncedEditor(TextArea):
    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if self.app is None:
            return
        try:
            results = self.app.query_one("#results", TextArea)
        except Exception:
            return
        if round(results.scroll_y) != round(new_value):
            results.scroll_to(y=new_value, animate=False, immediate=True)


class ResultsPanel(TextArea):
    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        app = self.app
        if not isinstance(app, AvocadoApp):
            return
        editor = app.query_one("#editor", TextArea)
        if round(new_value) != round(editor.scroll_y):
            self.scroll_to(y=editor.scroll_y, animate=False, immediate=True)

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        await super()._on_mouse_down(event)
        app = self.app
        if not isinstance(app, AvocadoApp):
            return
        button = getattr(event, "button", 1)
        if button != 1:
            return
        top = int(round(self.scroll_y))
        line_index = top + event.y
        app._last_clicked_result_line = line_index

    async def _on_mouse_up(self, event: events.MouseUp) -> None:
        await super()._on_mouse_up(event)
        app = self.app
        if not isinstance(app, AvocadoApp):
            return
        selection = self.selection
        if selection is None or selection.is_empty:
            return
        start_row = min(selection.start[0], selection.end[0])
        end_row = max(selection.start[0], selection.end[0])
        if not (0 <= start_row <= end_row < len(app._last_results_full)):
            return
        text = "\n".join(app._last_results_full[start_row : end_row + 1])
        app.copy_to_clipboard(text)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        app = self.app
        if not isinstance(app, AvocadoApp):
            return
        editor = app.query_one("#editor", TextArea)
        editor.scroll_to(y=max(0, editor.scroll_y - 3), animate=False, immediate=True)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        app = self.app
        if not isinstance(app, AvocadoApp):
            return
        editor = app.query_one("#editor", TextArea)
        editor.scroll_to(y=editor.scroll_y + 3, animate=False, immediate=True)


class Banner(Static):
    def on_mouse_down(self, event: events.MouseDown) -> None:
        app = self.app
        if not isinstance(app, AvocadoApp):
            return
        if app._file_path is None:
            return
        if event.y != 0:
            return

        w = self.size.width
        if w <= 0:
            return

        if event.x < app._banner_right_start_x:
            return

        file_name = app._file_path.name
        if not app._banner_path_visible:
            app.copy_to_clipboard(file_name)
            return

        if event.x >= app._banner_filename_start_x:
            app.copy_to_clipboard(file_name)
        else:
            app.copy_to_clipboard(str(app._file_path))


class Divider(Static):
    def on_mouse_down(self, event: events.MouseDown) -> None:
        app = self.app
        if not isinstance(app, AvocadoApp):
            return
        app._dragging = True
        self.set_class(True, "-dragging")
        self.capture_mouse()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        app = self.app
        if not isinstance(app, AvocadoApp):
            return
        if not app._dragging:
            return
        app._dragging = False
        self.set_class(False, "-dragging")
        self.release_mouse()
        save_results_width(app._file_path, app._results_width)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        app = self.app
        if not isinstance(app, AvocadoApp):
            return
        if not app._dragging:
            return

        results = app.query_one("#results", TextArea)
        screen_w = app.size.width
        new_width = screen_w - event.screen_x - 1
        max_results_width = max(
            MIN_RESULTS_WIDTH, screen_w - MIN_EDITOR_WIDGET_WIDTH - 1
        )
        app._results_width = max(
            MIN_RESULTS_WIDTH, min(MAX_RESULTS_WIDTH, max_results_width, new_width)
        )
        results.styles.width = app._results_width
        app.refresh(layout=True)
        app._evaluate_now()

    def render(self) -> Text:
        height = self.size.height
        if height <= 1:
            return Text("│")
        return Text("\n".join(["│"] * height))


class HorizontalSplit(Static):
    """Full-width horizontal split line with a right-aligned label.

    Renders a line of box-drawing characters across the screen with the
    label integrated on the right edge, similar to Mistral Vibe's
    "accept edits" line.
    """

    def __init__(self, label: str = "", **kwargs) -> None:
        super().__init__("", **kwargs)
        self._label = label

    def set_label(self, label: str) -> None:
        self._label = label
        self.refresh()

    def render(self) -> Text:
        width = self.size.width
        if width <= 0:
            return Text("")
        line_char = "─"
        text = Text()
        if not self._label:
            text.append(line_char * width)
            return text
        label_part = f" {self._label} "
        label_text = Text(label_part, style="bold", no_wrap=True)
        if label_text.cell_len >= width:
            label_text.truncate(width, overflow="ellipsis")
            return label_text
        line_len = max(0, width - label_text.cell_len)
        text.append(line_char * line_len)
        text.append_text(label_text)
        return text


class AvocadoApp(App):
    CSS = """
    Screen {
        background: $canvas;
        color: $ink;
    }

    #banner {
        height: 1;
        padding: 0 1;
        color: $ink_dim;
        overflow: hidden hidden;
        text-wrap: nowrap;
        text-overflow: clip;
    }

    #hsplit {
        height: 1;
        color: $rule;
    }

    #toolbar {
        height: 1;
        color: $rule;
    }

    #body {
        height: 1fr;
    }

    #editor {
        width: 1fr;
        min-width: $avocado-min-editor-width;
        height: 100%;
        border: none;
        background: $canvas;
    }

    #divider {
        width: 1;
        height: 100%;
        color: $rule;
    }

    #divider:hover {
        color: $rule_hover;
    }

    #divider.-dragging {
        color: $accent;
    }

    #results {
        width: 44;
        height: 100%;
        border: none;
        background: $canvas;
        color: $ink_dim;
        scrollbar-size: 0 0;
    }
    """

    def get_theme_variable_defaults(self) -> dict[str, str]:
        return {"avocado-min-editor-width": str(MIN_EDITOR_WIDGET_WIDTH)}


    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+c", "copy", "Copy"),
        ("ctrl+n", "new_file", "New file"),
        ("ctrl+s", "save", "Save"),
        ("ctrl+r", "toggle_results", "Toggle results"),
    ]

    def __init__(self, file_path: str | None = None) -> None:
        super().__init__()
        self.register_theme(DARK)
        self.register_theme(LIGHT)
        self._launch_dir = Path.cwd().resolve(strict=False)
        self._file_path = (
            Path(file_path).expanduser().resolve(strict=False) if file_path else None
        )
        self._eval_timer: Timer | None = None
        self._results_width: int = load_results_width(self._file_path)
        self._dragging = False
        self._results_visible = True
        self._banner_path_visible = True
        self._banner_right_start_x = 0
        self._banner_filename_start_x = 0
        self._last_results_full: list[str] = []
        self._last_clicked_result_line: int | None = None
        self._accent = palette_for(DARK.name)["accent"]
        self._muted = palette_for(DARK.name)["muted"]
        self.theme = theme_name_for(detect_scheme())

    def watch_theme(self, old_value: str, new_value: str) -> None:
        palette = palette_for(new_value)
        self._accent = palette["accent"]
        self._muted = palette["muted"]
        # This watcher fires from __init__ too, before the DOM exists.
        if self.is_running:
            self.call_after_refresh(self._update_banner)


    def compose(self) -> ComposeResult:
        with Vertical():
            yield Banner("", id="banner")
            yield HorizontalSplit(id="hsplit")
            with Horizontal(id="body"):
                yield SyncedEditor.code_editor(
                    "",
                    language=None,
                    theme="css",
                    soft_wrap=False,
                    show_line_numbers=False,
                    compact=True,
                    highlight_cursor_line=False,
                    id="editor",
                )
                yield Divider("│", id="divider")
                results = ResultsPanel.code_editor(
                    "",
                    language=None,
                    theme="css",
                    soft_wrap=False,
                    show_line_numbers=False,
                    read_only=True,
                    show_cursor=False,
                    highlight_cursor_line=False,
                    compact=True,
                    id="results",
                )
                results.can_focus = False
                results.show_vertical_scrollbar = False
                results.show_horizontal_scrollbar = False
                yield results
            yield HorizontalSplit(
                TOOLBAR_LABEL,
                id="toolbar",
            )

    def on_mount(self) -> None:
        self._apply_results_width()
        editor = self.query_one("#editor", TextArea)
        if self._file_path and self._file_path.exists():
            editor.text = self._file_path.read_text(encoding="utf-8")
        editor.focus()
        self._update_banner()
        self.call_later(self._update_banner)
        self._evaluate_now()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "editor":
            return
        self._schedule_evaluate()

    async def _on_resize(self, event: events.Resize) -> None:
        await super()._on_resize(event)
        self._evaluate_now()
        self._update_banner()
        self.call_later(self._update_banner)

    def action_new_file(self) -> None:
        base_dir = self._file_path.parent if self._file_path else self._launch_dir
        suggested = str(base_dir / "untitled.txt")
        self.push_screen(
            PathPromptScreen(
                suggested,
                title="Enter document name and path",
                placeholder="path\\to\\untitled.txt",
                path_help_text=f"Relative paths start from {self._launch_dir} and save as .txt",
                submit_text="Enter=create",
                cancel_text="Esc=cancel",
            ),
            self._new_file_callback,
        )

    def _new_file_callback(self, path: Optional[str]) -> None:
        if not path:
            return

        self._file_path = self._resolve_user_path(self._ensure_txt_path(path))
        editor = self.query_one("#editor", TextArea)
        editor.text = ""
        editor.focus()
        self._update_banner()
        self._evaluate_now()

    def action_save(self) -> None:
        suggested = str(self._file_path or (self._launch_dir / "untitled.txt"))
        self.push_screen(
            PathPromptScreen(
                suggested,
                title="Enter document name and path",
                placeholder="path\\to\\document.txt",
                path_help_text=f"Relative paths start from {self._launch_dir} and save as .txt",
                submit_text="Enter=save",
                cancel_text="Esc=cancel",
            ),
            self._save_as_callback,
        )

    def _save_as_callback(self, path: Optional[str]) -> None:
        if not path:
            return
        self._file_path = self._resolve_user_path(self._ensure_txt_path(path))
        self._update_banner()
        editor = self.query_one("#editor", TextArea)
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(editor.text, encoding="utf-8")

    def _ensure_txt_path(self, raw_path: str) -> str:
        path = Path(raw_path.strip()).expanduser()
        if path.suffix.lower() == ".txt":
            return str(path)
        if path.suffix:
            return str(path.with_suffix(".txt"))
        return str(path.with_name(path.name + ".txt"))

    def _resolve_user_path(self, raw_path: str) -> Path:
        path = Path(raw_path.strip()).expanduser()
        if path.is_absolute():
            return path.resolve(strict=False)
        return (self._launch_dir / path).resolve(strict=False)

    def _schedule_evaluate(self) -> None:
        if self._eval_timer is not None:
            self._eval_timer.stop()
        self._eval_timer = self.set_timer(0.15, self._evaluate_now)

    def _evaluate_now(self) -> None:
        editor = self.query_one("#editor", TextArea)
        results = self.query_one("#results", TextArea)

        user_lines = editor.text.split("\n")
        out = evaluate_source_linewise(
            build_prelude_source() + editor.text
        )
        user_outputs = out.lines[prelude_line_count():]
        if len(user_outputs) < len(user_lines):
            user_outputs = user_outputs + [""] * (len(user_lines) - len(user_outputs))
        else:
            user_outputs = user_outputs[: len(user_lines)]
        self._last_results_full = user_outputs
        width = max(1, self._results_width - 1)
        cropped = truncate_lines(user_outputs, width)
        results.text = "\n".join(cropped)
        if round(results.scroll_y) != round(editor.scroll_y):
            results.scroll_to(y=editor.scroll_y, animate=False, immediate=True)

    def _copy_result_line(self, line_index: int) -> None:
        if line_index < 0 or line_index >= len(self._last_results_full):
            return
        self.copy_to_clipboard(self._last_results_full[line_index])

    def action_copy(self) -> None:
        if self._results_visible:
            results = self.query_one("#results", TextArea)
            selection = results.selection
            if selection is not None and not selection.is_empty:
                start_row = min(selection.start[0], selection.end[0])
                end_row = max(selection.start[0], selection.end[0])
                if 0 <= start_row <= end_row < len(self._last_results_full):
                    text = "\n".join(self._last_results_full[start_row : end_row + 1])
                    self.copy_to_clipboard(text)
                    return
            if self._last_clicked_result_line is not None:
                self._copy_result_line(self._last_clicked_result_line)
                return
        editor = self.query_one("#editor", TextArea)
        try:
            editor.action_copy()
        except Exception:
            return

    def _apply_results_width(self) -> None:
        results = self.query_one("#results", TextArea)
        divider = self.query_one("#divider", Static)
        max_results_width = max(
            MIN_RESULTS_WIDTH, self.size.width - MIN_EDITOR_WIDGET_WIDTH - 1
        )
        self._results_width = max(
            MIN_RESULTS_WIDTH, min(MAX_RESULTS_WIDTH, max_results_width, self._results_width)
        )
        if self._results_visible:
            results.styles.display = "block"
            results.styles.width = self._results_width
            divider.styles.display = "block"
        else:
            results.styles.display = "none"
            divider.styles.display = "none"
        self.refresh(layout=True)
        self._evaluate_now()
        self._update_banner()

    def action_toggle_results(self) -> None:
        self._results_visible = not self._results_visible
        self._apply_results_width()

    def _truncate_banner_text(self, text: str, width: int) -> str:
        if width <= 0:
            return ""
        if cell_len(text) <= width:
            return text
        if width == 1:
            return "…"
        return text[: width - 1] + "…"

    def _truncate_filename(self, file_name: str, width: int) -> str:
        if width <= 0:
            return ""
        if cell_len(file_name) <= width:
            return file_name
        suffix = Path(file_name).suffix
        if suffix and cell_len(suffix) + 2 <= width:
            prefix_w = width - cell_len(suffix) - 1
            return file_name[:prefix_w] + "…" + suffix
        return self._truncate_banner_text(file_name, width)

    def _update_banner(self) -> None:
        banner = self.query_one("#banner", Static)
        content_w = max(1, banner.content_region.width, self.size.width - 2)
        app_name = "Avocado's Constant"
        app_name_w = cell_len(app_name)

        first_line = Text(no_wrap=True)

        if self._file_path is None:
            first_line.append(app_name, style=self._accent)
            self._banner_right_start_x = content_w
            self._banner_filename_start_x = content_w
            self._banner_path_visible = False
        else:
            path_str = str(self._file_path)
            file_name = self._file_path.name
            available_right_w = max(1, content_w - (app_name_w + 2))
            self._banner_path_visible = cell_len(path_str) <= max(0, available_right_w - 1)

            if self._banner_path_visible:
                right_display = path_str
            else:
                right_display = self._truncate_filename(file_name, available_right_w)
            right_display_w = cell_len(right_display)
            right_display_used_w = min(available_right_w, right_display_w) + 1
            gap_w = max(1, content_w - app_name_w - right_display_used_w)

            first_line.append(app_name, style=self._accent)
            first_line.append(" " * gap_w)
            self._banner_right_start_x = app_name_w + gap_w

            if self._banner_path_visible:
                first_line.append(right_display, style=self._muted)
                idx = right_display.rfind("\\")
                if idx == -1:
                    idx = right_display.rfind("/")
                if idx != -1 and idx + 1 < len(right_display):
                    self._banner_filename_start_x = self._banner_right_start_x + idx + 1
                    first_line.stylize(
                        self._accent,
                        self._banner_filename_start_x,
                        self._banner_right_start_x + len(right_display),
                    )
                else:
                    self._banner_filename_start_x = self._banner_right_start_x
            else:
                self._banner_filename_start_x = self._banner_right_start_x
                first_line.append(right_display, style=self._accent)
            first_line.append(" ")

        if first_line.cell_len < content_w:
            first_line.append(" " * (content_w - first_line.cell_len))

        banner.update(first_line)
