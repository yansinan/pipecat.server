# Comparison-Driven Cleanup — Find the Canonical Reference, Diff Against It

## When to use this technique

When you've iterated on a server/pipeline/wrapper file for a while and now
suspect some of it is redundant — duplicated framework internals, dead routes,
or custom reimplementations of things the framework already provides.

The signal is usually a session like this one: the user says "对照着 X 把你
造的多余的轮子删掉" (compare against X and delete redundant wheels) or
"看看 Y 怎么处理这件事的" (see how Y handles this).

## The method

### 1. Find two comparable files in your workspace

Most projects end up with TWO implementations of the same role:

| Type | Name pattern | Trustworthiness |
|---|---|---|
| **Upstream reference** | `pipecat-examples/.../bot.py`, `small-webrtc-prebuilt/test/bot.py`, framework samples | High — known-good, frequently updated |
| **In-flight copy** | `src/server_prebuilt.py`, `src/bot.py` | Lower — has accumulated cruft |

Pick the smaller, more focused upstream file as the canonical reference.

### 2. Side-by-side diff checklist

Open both files. For each block in your in-flight copy, check:

| Question | If yes, action |
|---|---|
| Does the reference also define this custom handler? | If NO → check framework; probably redundant |
| Does the reference use a framework method you reinvented? | Replace with framework call |
| Does the reference register fewer routes than you? | Drop unused routes |
| Does the reference use `connection.pc_id` instead of UUIDs for logs? | Adopt |
| Does the reference have CORS middleware you don't? | Add only if client is cross-origin |
| Does the reference call `logging.basicConfig()` alongside loguru? | Drop — loguru owns logging |
| Does the reference carry a parameter "just for logging"? | Remove param, use framework's own identifier |

### 3. Worked example: server_prebuilt.py cleanup (2026-06-30)

**Reference**: `src/bot_js_client.py` (246 lines, known-good, runs the same
SmallWebRTC + PrebuiltUI stack on a different port).

**In-flight**: `src/server_prebuilt.py` (197 lines, accumulated custom code).

After diff:

| Custom code | Reference behavior | Action |
|---|---|---|
| `_handle_ice_patch()` (17 lines, manual `candidate_from_sdp` + `_pcs_map`) | `webrtc_handler.handle_patch_request()` | Delete |
| `offer()` + `ice_candidate()` — bare `/api/offer` POST/PATCH | Reference only has `/sessions/{id}/api/offer` | Delete bare paths |
| `_handle_offer(session_id=...)` UUID plumbing | Reference uses `connection.pc_id` | Drop param |
| `import logging; logging.basicConfig(...)` | Reference has neither | Delete |
| `CORSMiddleware` block | PrebuiltUI mounts on same FastAPI — same origin | Delete |
| `from pipecat.workers.runner import WorkerRunner` inside `_run_pipeline` | Reference imports at top | Move to module top |

Result: 197 → 134 lines (-32%). Verified end-to-end (browser connect → text
send → assistant reply in conversation panel). No regression.

### 4. Anti-patterns this catches

- **Reinventing handlers** — writing `_handle_ice_patch()` because you didn't
  grep the framework for `handle_patch_request()`. Discovered: 19-line custom
  function was a literal copy of `request_handler.py:handle_patch_request`.

- **Route proliferation** — registering both `/api/offer` and
  `/sessions/{id}/api/offer` "just to be safe". Discovered: bare path was
  dead code (only `@pipecat-ai/small-webrtc-transport >=1.10` clients in use).

- **Logging duplication** — calling `logging.basicConfig()` alongside loguru.
  Discovered: redundant; loguru intercepts stdlib logging.

- **Connection-ID shadowing** — generating UUIDs for logs when the framework
  already provides `connection.pc_id`. Discovered: the UUIDs were never
  cross-referenced with anything; only used in log lines.

### 5. When NOT to clean up

Some features only exist in YOUR version. The comparison-driven cleanup
should NOT delete them:

- **Opening greeting** via `on_client_ready → LLMRunFrame()` — if the
  reference doesn't have it but you do, it's because you wanted the bot to
  speak first. KEEP.

- **Test audio injection** — only your version injects PCM for headless
  verification. KEEP.

- **CORS middleware** — if your client is on a different port (e.g.
  vanilla JS client on 5173, server on 7860), KEEP. Only remove if
  same-origin (PrebuiltUI mounted on same FastAPI app).

- **Endpoint additions** beyond what the reference shows (e.g. custom
  `/inject_test_audio`, `/events`) — legitimate app-specific features.

### 6. Verification after cleanup

After removing code, run the same end-to-end test that was passing before:

1. Start server
2. Browser connects
3. Send a message (audio or text)
4. Verify server logs show: `Client ready via RTVI` → LLM `Generating chat`
   → `Bot started speaking` → user-visible reply in chat panel

If any of those go missing, the cleanup removed load-bearing code. Restore
the smallest piece that restores the chain.

### 7. Update the template too

If your project ships a `templates/server_prebuilt.py` (canonical starter),
update it to match the cleaned-up version. Future sessions that copy the
template get the right shape immediately, no cleanup needed.