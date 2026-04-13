---
name: agent-browser
description: Browser automation CLI — navigate pages, take screenshots, click elements, fill forms, extract CSS, and check console errors.
invoke_when: "Task requires browser interaction — navigating to a URL, taking screenshots, clicking elements, filling forms, checking console errors, or testing a web page. This is the primary skill for any task that involves operating a browser."
---

# agent-browser

Browser automation CLI. Commands follow this pattern:

```bash
agent-browser [flags] <command> [args]

# View all available commands and options:
agent-browser --help
agent-browser <command> --help   # Help for a specific command
```

## Core Workflow

```bash
# Step 1: Navigate and wait — chain these, no intermediate output needed
agent-browser  open <url> && agent-browser  wait --load networkidle

# Step 2: Get interactive elements with refs — run separately to read output
agent-browser  snapshot -i
# Output: @e1 [button] "Submit", @e2 [input] placeholder="Email", ...

# Step 3: Interact using refs — chain when no output needed between steps
agent-browser  fill @e2 "text" && agent-browser  click @e1

# Step 4: Re-snapshot after navigation or DOM changes — run separately to read new refs
agent-browser  snapshot -i
```

**IMPORTANT: Refs (`@e1`, `@e2`, ...) are invalidated when the page changes. Always re-snapshot after clicking links, submitting forms, or any action that changes the page.**

---

## Command Chaining — PREFERRED

**Always chain independent commands with `&&` in a single bash call.** The browser persists between commands via a background daemon, so chaining is safe. This minimizes the number of bash tool calls.

```bash
# Navigate + wait + screenshot in one call
agent-browser  open <url> && \
agent-browser  wait --load networkidle && \
agent-browser  set viewport 1440 900 && \
agent-browser  screenshot /path/to/output.png

# Multiple interactions in one call
agent-browser  fill @e1 "user@example.com" && \
agent-browser  fill @e2 "password123" && \
agent-browser  click @e3 && \
agent-browser  wait --load networkidle
```

**When to run separately** (only when you need to read the output before proceeding):
- `snapshot -i` → parse the refs → then interact using those refs
- `get url` / `get text` → read the value → then branch on it

Everything else: chain it.

---

## Navigation

```bash
agent-browser  open <url>       # Navigate to URL
agent-browser  back             # Go back
agent-browser  forward          # Go forward
agent-browser  reload           # Reload page
agent-browser  close            # Close browser
```

---

## Snapshot (Getting Element Refs)

```bash
agent-browser  snapshot -i             # Interactive elements only (RECOMMENDED)
agent-browser  snapshot -i -C          # Also include cursor-interactive divs/spans
agent-browser  snapshot -s "#selector" # Scope to CSS selector
agent-browser  snapshot -i -c          # Compact output
```

Snapshot output format:
```
@e1 [button] "Submit"
@e2 [input type="email"] placeholder="Email"
@e3 [a href="/about"] "About"
```

---

## Interaction (use @refs from snapshot)

```bash
agent-browser  click @e1           # Click element
agent-browser  click @e1 --new-tab # Click and open in new tab
agent-browser  fill @e2 "text"     # Clear and type text
agent-browser  type @e2 "text"     # Type without clearing
agent-browser  select @e3 "value"  # Select dropdown option
agent-browser  check @e4           # Check checkbox
agent-browser  hover @e5           # Hover over element
agent-browser  press Enter         # Press key (Enter, Tab, Escape, etc.)
agent-browser  scroll down 500     # Scroll page
agent-browser  scrollintoview @e1  # Scroll element into view
```

---

## Viewport

```bash
agent-browser  set viewport 1440 900    # Standard desktop
agent-browser  set viewport 2560 1440   # High-res desktop
agent-browser  set media dark           # Dark mode
agent-browser  set media light          # Light mode
```

---

## Screenshots

```bash
agent-browser  screenshot <path>           # Viewport screenshot
agent-browser  screenshot --full <path>    # Full page screenshot
agent-browser  screenshot --annotate       # Annotated (numbered elements overlaid)
```

**For element-scoped screenshots** (no direct uid support):
```bash
agent-browser  scrollintoview @eN
agent-browser  screenshot <path>
```

---

## Wait

```bash
agent-browser  wait --load networkidle   # Wait for network idle (best for page loads)
agent-browser  wait --text "Welcome"     # Wait for text to appear
agent-browser  wait --url "**/dashboard" # Wait for URL pattern
agent-browser  wait @e1                  # Wait for element to be visible
agent-browser  wait 2000                 # Wait milliseconds (last resort)
```

---

## Get Information

```bash
agent-browser  get text @e1      # Get element text
agent-browser  get url           # Get current URL
agent-browser  get title         # Get page title
agent-browser  get styles @e1    # Get computed CSS styles (font, color, bg, etc.)
agent-browser  get attr @e1 href # Get attribute value
```

---

## Tabs (Multi-Page)

```bash
agent-browser  tab new <url>   # Open URL in new tab
agent-browser  tab             # List open tabs
agent-browser  tab 1           # Switch to tab by index (0-based)
agent-browser  tab close       # Close current tab
agent-browser  tab close 2     # Close tab by index
```

---

## Console and Errors

```bash
agent-browser  errors          # View uncaught JS exceptions
agent-browser  errors --clear  # Clear error log
agent-browser  console         # View console messages (log, warn, error)
agent-browser  console --clear # Clear console
```

---

## JavaScript Evaluation

For simple expressions:
```bash
agent-browser  eval 'document.title'
```

For complex/multiline scripts, use `--stdin` with heredoc to avoid shell quoting issues:
```bash
agent-browser  eval --stdin <<'EVALEOF'
JSON.stringify(
  ['body','h1','h2','p','a','button'].reduce((acc, sel) => {
    const el = document.querySelector(sel);
    if (!el) return acc;
    const s = getComputedStyle(el);
    acc[sel] = { color: s.color, backgroundColor: s.backgroundColor, fontFamily: s.fontFamily };
    return acc;
  }, {})
)
EVALEOF
```

**Use `--stdin` for any script with nested quotes, arrow functions, or template literals.**

---

## Annotated Screenshots (Vision Mode)

```bash
agent-browser  screenshot --annotate
# Output includes image path + legend:
#   [1] @e1 button "Submit"
#   [2] @e2 link "Home"
# Use refs immediately after — they are cached
agent-browser  click @e1
```

Use annotated screenshots when:
- Page has unlabeled icon buttons or visual-only elements
- You need to see spatial layout and element positions
- Canvas or chart elements are present (invisible to text snapshots)

---

## Deep-Dive References

| Reference | When to Use |
|-----------|-------------|
| [references/commands.md](references/commands.md) | Full command reference with all options and flags |
| [references/snapshot-refs.md](references/snapshot-refs.md) | Ref lifecycle, invalidation rules, troubleshooting |
