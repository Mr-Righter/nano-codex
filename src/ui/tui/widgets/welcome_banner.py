"""ASCII banner widget shown at the start of an interactive Nano-Codex session."""

from __future__ import annotations

from pathlib import Path

from textual import containers
from textual.app import ComposeResult
from textual.widgets import Static


class WelcomeBanner(Static):
    """ASCII art welcome banner shown at session start."""

    DEFAULT_CSS = """
    WelcomeBanner {
        padding: 0 2;
        margin: 1 1;
        height: auto;
        color: $text;
    }
    WelcomeBanner .corner-row {
        layout: horizontal;
        height: 1;
        width: 1fr;
    }
    WelcomeBanner .corner {
        width: 3;
        color: rgb(63, 128, 190);
        text-style: bold;
    }
    WelcomeBanner .corner-fill {
        width: 1fr;
    }
    WelcomeBanner #body {
        padding: 0 2;
        height: auto;
    }
    WelcomeBanner #art {
        color: rgb(63, 128, 190);
        text-style: bold;
    }
    WelcomeBanner #meta-col {
        layout: vertical;
        height: auto;
        width: auto;
    }
    WelcomeBanner .meta-line {
        width: auto;
        padding: 0 1;
        background: #0f1b22;
        text-style: bold;
        text-align: left;
    }
    WelcomeBanner #model {
        margin-bottom: 0;
    }
    WelcomeBanner #dir {
        width: auto;
    }
    """

    LOGO = (
    "███┐   ██┐ █████┐ ███┐   ██┐ ██████┐      ██████┐ ██████┐ ██████┐ ███████┐██┐  ██┐\n"
    "████┐  ██│██┌──██┐████┐  ██│██┌───██┐    ██┌────┘██┌───██┐██┌──██┐██┌────┘└██┐██┌┘\n"
    "██┌██┐ ██│███████│██┌██┐ ██│██│   ██│    ██│     ██│   ██│██│  ██│█████┐   └███┌┘ \n"
    "██│└██┐██│██┌──██│██│└██┐██│██│   ██│    ██│     ██│   ██│██│  ██│██┌──┘   ██┌██┐ \n"
    "██│ └████│██│  ██│██│ └████│└██████┌┘    └██████┐└██████┌┘██████┌┘███████┐██┌┘ ██┐\n"
    "└─┘  └───┘└─┘  └─┘└─┘  └───┘ └─────┘      └─────┘ └─────┘ └─────┘ └──────┘└─┘  └─┘"
    )

    def __init__(self, model: str = "unknown", work_dir: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._model = model
        resolved_work_dir = Path(work_dir).expanduser().resolve() if work_dir else Path.cwd().resolve()
        self._work_dir = self._format_work_dir(resolved_work_dir)

    @staticmethod
    def _format_work_dir(path: Path) -> str:
        """Render work_dir with `~` when it lives under the current user's home directory."""
        home = Path.home().expanduser().resolve()
        try:
            relative = path.relative_to(home)
        except ValueError:
            return str(path)
        return "~" if not relative.parts else f"~/{relative.as_posix()}"

    @staticmethod
    def format_meta_line(label: str, value: str) -> str:
        return f"[rgb(63,128,190)]{label:<7}[/rgb(63,128,190)] [white]{value}[/white]"

    def compose(self) -> ComposeResult:
        with containers.Horizontal(classes="corner-row"):
            yield Static("┌─", classes="corner")
            yield Static("", classes="corner-fill")
            yield Static("─┐", classes="corner")
        with containers.Vertical(id="body"):
            yield Static(self.LOGO, id="art")
            with containers.Vertical(id="meta-col"):
                yield Static(self.format_meta_line("model:", self._model), id="model", classes="meta-line")
                yield Static(self.format_meta_line("dir:", self._work_dir), id="dir", classes="meta-line")
        with containers.Horizontal(classes="corner-row"):
            yield Static("└─", classes="corner")
            yield Static("", classes="corner-fill")
            yield Static("─┘", classes="corner")
