# Chrome CDP: Picking the Right Tab When User Has Many Tabs Open

## Symptom
You call `browser_navigate` with `http://localhost:8766/`, and the snapshot says title is "DevTools" or "GitHub" or "Headroom Dashboard" — but you asked for your own app's URL.

## Root Cause
The hermes browser tool shares a Chrome instance with the user. When the user has 5+ tabs open, `browser_navigate` lands on whichever tab the browser was last focused on, NOT a new tab and NOT necessarily the URL you specified.

`browser_navigate` does call `Page.navigate` on its `target_id`, but its target tracking may not be the same as what you think.

## Solution: List targets, then target by ID

```python
# Step 1 — list all tabs to find yours
browser_cdp(method="Target.getTargets", params={})

# Returns: list of targetInfos with targetId, title, url
# Example response:
# {
#   "targetInfos": [
#     {"targetId": "3DFF33...", "type": "page", "title": "Pipecat UI", "url": "http://localhost:8766/client/"},
#     {"targetId": "F675820...", "type": "page", "title": "Pipecat Test", "url": "http://localhost:8765/"},
#     {"targetId": "5DCD096...", "type": "page", "title": "edge-tts examples", "url": "https://github.com/..."},
#     ...
#   ]
# }

# Step 2 — use the target_id matching your URL/title
browser_cdp(
    method="Runtime.evaluate",
    params={"expression": "document.title"},
    target_id="3DFF33DCF1C4BEFE48CE25972953A309"  # ← pass the right one
)
```

## How to identify "your" tab

When debugging your own app:
- Filter by URL substring (e.g. `localhost:8766`)
- Or by title (e.g. your app name)
- Or by content (e.g. `<title>` tag)

The `Target.getTargets` response includes both `url` and `title` — pick the most reliable.

## Re-navigating if your tab drifted

If the user switched to another tab in between your `browser_navigate` calls, your target_id still points to your old tab but it's no longer "the current focused tab". Two options:

```python
# Option 1 — bring your tab to the front (focus it)
browser_cdp(
    method="Target.activateTarget",
    params={"targetId": "3DFF33..."},
)

# Option 2 — just navigate that tab to your URL again (no need to focus)
browser_navigate(url="http://localhost:8766/")  # but this may land on a DIFFERENT tab
```

`Target.activateTarget` is the reliable way.

## When the response is "wasn't found"

Some methods only work on the currently focused tab:
```
{"error": {"code": -32601, "message": "'Runtime.evaluate' wasn't found"}}
```

This means your target_id is no longer valid (tab closed) or the method needs a Page context. Re-list targets and pick again.

## Critical: browser_console targets the DevTools page, not your app

**Symptom**: `browser_navigate` shows the correct page (snapshot shows your app's
UI), but `document.getElementById('transport-select')` returns null. 89 DOM
elements, only 4 have ids. The HTML served by Vite clearly has 8+ elements with
id=, yet the browser console finds none.

**Root cause**: The Hermes CDP browser opens Chrome with DevTools enabled. When
you navigate, TWO tabs are created: (1) your application page and (2) a hidden
DevTools page (`devtools://devtools/bundled/devtools_app.html?panel=elements`).
**The `browser_console` tool evaluates JavaScript in the DevTools page, not in
your application page.**

Evidence:
```python
browser_console(expression="location.href")
# Returns: "devtools://devtools/bundled/devtools_app.html?..."
```

The DevTools page's `document` has no elements from your application. All
`getElementById` calls return null. The `browser_navigate` snapshot works because
it targets the application tab via the accessibility tree (which captures the
correct page).

**Fix — close DevTools before using browser_console:**
```python
# 1. List all targets to find the DevTools tab
browser_cdp(method="Target.getTargets", params={})
# → targets show both your app page AND a devtools:// page

# 2. Close the DevTools tab
browser_cdp(method="Target.closeTarget", params={"targetId": "A96A6..."})
```

**WARNING**: Once you call `Target.closeTarget` or `Target.attachToTarget` via
`browser_cdp`, the `browser_console` tool **dies permanently for the rest of
the session**:
```
RuntimeError: CDP error on id=N: {'code': -32001, 'message': 'Session with given id not found.'}
```
`browser_navigate` and `browser_snapshot` still work, but console execution is
broken. The CDP supervisor's internal session mapping cannot be re-created from
inside the agent.

**Standard workflow**: Do all browser_console work BEFORE any browser_cdp
Target method calls. If browser_console dies, the user must run
`/browser close` + `/browser connect` to restart the CDP session.

## Symbiosis: `browser_console` and `browser_cdp` use different target tracking

The `browser_console(expression=...)` and `browser_click(ref=...)` tools use a SEPARATE target tracking from `browser_cdp`. They have their own "active tab" concept that follows whatever the user last clicked. If you want to drive a specific tab, prefer `browser_cdp` with explicit `target_id` — it's deterministic.

`browser_navigate(url=...)` should open a NEW tab or focus an existing one, but in practice with shared Chrome instances it sometimes lands on the wrong tab. After `browser_navigate`, always verify with `browser_cdp(method="Runtime.evaluate", params={"expression": "location.href"}, target_id=...)` that you actually landed where you intended.

## Real session example (this project's debugging)

The user had 8+ tabs open:
- GitHub repos (pipecat-ai, rany2/edge-tts, pipecat-examples)
- Headroom dashboard
- Two pipecat PrebuiltUI pages (8765 and 8766) — they were testing two transports
- Chrome devtools panels

Default `browser_navigate` kept landing on the devtools tab. Solution: pick the explicit `target_id` from `Target.getTargets` matching `localhost:8766`.

## Sanity check before every browser test

```python
# 1. List targets
targets = browser_cdp(method="Target.getTargets")
# 2. Find yours
my_target = next(
    t["targetId"] for t in targets["result"]["targetInfos"]
    if "localhost:8766" in t["url"] and t["type"] == "page"
)
# 3. Activate
browser_cdp(method="Target.activateTarget", params={"targetId": my_target})
# 4. Verify
browser_cdp(
    method="Runtime.evaluate",
    params={"expression": "location.href"},
    target_id=my_target,
)
```
