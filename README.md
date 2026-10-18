# Avocado's Constant

Terminal-native math notebook: edit a `.txt` document on the left, read results per line on the right. The evaluator is math-first but accepts Python, MATLAB-like, LaTeX-like, and Desmos-style input in the same document.

The backend is Python: `textual` for the TUI, `numpy` for values, `sympy` plus an ANTLR grammar for parsed LaTeX. Requires Python 3.10+.

## Screenshots

**Dark mode** — the default appearance, following your terminal's dark theme:

![Dark mode](docs/screenshot-dark.png)

**Light mode** — picked up automatically when your terminal uses a light background:

![Light mode](docs/screenshot-light.png)

## Install

**Fast install** — downloads the repo, installs into a dedicated virtual environment, and puts an `avocado` launcher on your PATH. The installer adds `~/.local/bin` to your shell startup files (`~/.zshrc`, `~/.bashrc`, `~/.profile`, fish config) when it is missing, so `avocado` works from any directory in new terminals. Re-run it any time to update to the latest `main`.

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/ProtonKicker/Avocado/main/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/ProtonKicker/Avocado/main/install.ps1 | iex
```

**Editable install** — for working on the code itself:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

## Run

```bash
avocado examples/demo.txt
```

Equivalently: `python -m avocado_tui.__main__ examples/demo.txt`, or `./avocado.sh examples/demo.txt` (`.\avocado.ps1 examples\demo.txt` on Windows) — the wrapper scripts create and use the repository's own `.venv`.

Running `avocado` with no path opens an empty document. A path that does not exist is created, a missing extension becomes `.txt`, missing parent directories are created, and relative paths resolve from the directory `avocado` was launched in. Saving from the UI always writes `.txt`.

## Notebook syntax

One document can mix all of these; results are computed line by line.

```text
x = 3
sin(pi / 2)
1:1:5
[1 2 3; 4 5 6]
\frac{1}{2}
\sum_{i=1}^{4} i
e^x
```

- Desmos-style basic math: `sin(3)`, `cos(3)`, `sqrt(4)`, `pi`, `e`, `ln(10)`, `e^x`
- MATLAB-like entry: ranges such as `1:1:10`, matrix literals such as `[1 2 3; 4 5 6]`
- LaTeX parsed by sympy: `\frac{1}{2}`, `\sqrt[3]{8}`, `\sum` / `\prod`, `\alpha`, `\theta_1`, `\vec{a} \cdot \vec{b}`, `\vec{a} \times \vec{b}`
- Python: assignments, expressions, and small multi-line blocks — `def`, `for`, and `if` execute as one chunk

These are practical entry syntaxes, not full Desmos, MATLAB, or LaTeX support: evaluation stops at the first error and later lines show `Skipped`.

## Theme

The notebook follows your **terminal's** light/dark appearance. At startup it asks
the terminal for its own background colour (the OSC 11 escape query that Kitty,
iTerm2, Windows Terminal, Alacritty, WezTerm and Ghostty all answer), classifies
it by luminance, and picks the matching palette — the dark palette is the same
one the app has always used. Terminals that do not answer the query fall back to
the `COLORFGBG` environment variable, and then to dark. Because the terminal's
colour is read when `avocado` starts, changing your terminal's theme recolours
documents opened afterwards; switch the terminal profile or restart to pick up
the new appearance.

## Keys

- Ctrl+S save, Ctrl+N new document, Ctrl+R toggle the results panel, Ctrl+Q quit, Esc close the path prompt
- Ctrl+C copies the selected results, or the result line you last clicked
- Click the banner path to copy it: the filename when you click the name, the full path when you click the directory
- Drag the vertical divider to resize the results panel; the width is remembered per document in `~/.avocado/config.json`, so a terminal with mouse support is needed for that one gesture

## Uninstall

Remove the launcher and the install directory — `~/.local/bin/avocado` plus `~/.local/share/avocado` on macOS/Linux, `%LOCALAPPDATA%\avocado\bin` plus `%LOCALAPPDATA%\avocado` on Windows. Your documents and `~/.avocado/config.json` are separate and are kept until you delete them.
