# Slash Commands

Slash commands are interactive controls for the TUI runtime. They are resolved before user text is sent to the agent, so they are best for local workflow actions rather than model-visible tool use.

## Built-In Commands

| Command | Purpose |
| --- | --- |
| `/compact` | Force one manual compaction pass for the active session. |
| `/clear` | Clear non-system history while keeping the current system context. |
| `/model` | Open the interactive model picker. |
| `/exit` | Quit Nano-Codex. |

## How Slash Commands Work

1. TUI input beginning with `/` is intercepted before normal agent handling.
2. `SlashCommandRegistry` in `src/ui/tui/slash_registry.py` resolves the handler.
3. The handler receives `SlashCommandContext`, which currently exposes the active `InteractiveWorkflow`.
4. The handler returns `None`, an informational string, or `"exit"`.
5. `REGISTRY.all()` also feeds command autocomplete.

Slash commands are interactive-only. They are not part of the agent tool surface and they do not appear inside the model-visible agent loop.

## Minimal Command

```python
from src.ui.tui.slash_registry import REGISTRY, SlashCommandContext


@REGISTRY.command("/hello", "Show a small greeting")
async def _cmd_hello(ctx: SlashCommandContext) -> str | None:
    del ctx
    return "Hello from Nano-Codex."
```

Registering the command is enough for both dispatch and autocomplete.

## Workflow-Aware Command

Use `ctx.workflow` when the command needs access to session state, the active agent, or TUI controls.

```python
from src.ui.tui.slash_registry import REGISTRY, SlashCommandContext


@REGISTRY.command("/session-path", "Show the active session file path")
async def _cmd_session_path(ctx: SlashCommandContext) -> str | None:
    path = ctx.workflow.history_file
    if path is None:
        return "No history file is configured."
    return f"Active session file: {path}"
```

The same pattern can be used for future interactive commands such as workflow helpers, pickers, or session controls.

## When to Use Slash Commands

Use a slash command when the behavior is tied to:

- interactive workflow control
- session lifecycle operations
- TUI-only affordances such as pickers or transcript actions
- local runtime behavior that should not be exposed as a tool call

Use a tool instead when the action should stay visible to the model as part of normal reasoning.

## Project Pattern

- Keep slash commands short and workflow-scoped.
- Use `ctx.workflow.ui.controls` for TUI-only UI actions.
- If a command mutates session state, persist it just as `/compact` and `/clear` do.
- Prefer tools when the behavior belongs inside the agent loop.
