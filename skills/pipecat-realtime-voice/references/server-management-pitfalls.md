# Server Management Pitfalls — Multiple Servers, Log Access, killall Scope

## Pitfall 1: Multiple server_prebuilt instances on the same port

If you start the server once via hermes background session, then later kill that session and start again via your terminal, BOTH can be running. `kill` on one PID leaves the other alive.

**Symptom**: `ss -tlnp | grep 8766` shows one listener, but `ps -ef | grep server_prebuilt` shows two python processes. Browser connects to whichever owns the listener.

**How it happens in this project**:
1. Hermes session runs `python -m src.server_prebuilt > .cache-uv/server.log 2>&1 &` (background, log to file)
2. You press Ctrl+C in your terminal session — but your terminal session is a DIFFERENT shell that started a SECOND server via `bash restart_server.sh` (logs go to pts/22 instead of file)
3. Now both run. `killall -9 python` would kill BOTH, including `bot.py` on 8765 (another service owned by a different session)

**Fix — scope the killall to your process name only**:

```bash
# BAD — kills all python (8765 bot.py too):
killall -9 python

# GOOD — only kills server_prebuilt:
killall -9 -r "python.*server_prebuilt"
```

**Diagnostic**:
```bash
# All server_prebuilt processes:
ps -ef | grep "python.*server_prebuilt" | grep -v grep

# Owner of the listener (whichever process has the socket open):
ss -tlnp | grep 8766
```

If `ps` shows two and `ss` shows one, the other one has been killed (no socket) but is in `Sl` state finishing cleanup, or is zombie. Wait ~5s and recheck.

## Pitfall 2: User-terminal logs are not in your readable files

When you (hermes) start a process via `terminal(background=true)`, its stdout/stderr is captured by hermes — you can read it via `process(action='log')`.

When **the user** runs a command in **their terminal** (e.g. `bash restart_server.sh` in pts/22), the output goes to pts/22 (the terminal scrollback). You CANNOT read it via `process(action='log')` because hermes doesn't own that session.

**Symptom**: Server runs (port 8766 is open), browser connects fine, but you have no idea what the server is logging.

**Fixes**:
- **Option A**: Ask the user to copy-paste the last 20-30 lines from their terminal
- **Option B**: Modify the restart script to also tee to a file:
  ```bash
  uv run --project . python -m src.server_prebuilt 2>&1 | tee -a /home/dr/workspace/pipecat/.cache-uv/server.log
  ```
- **Option C**: Have the user run the server in a separate file from the start: `python -m src.server_prebuilt > server.log 2>&1 &`

**Don't** assume hermes's tracked session log file is the only source — when in doubt, check both the user terminal and the log file.

## Pitfall 3: `killall` matching on a wrong process group

`killall -9 -r "python.*server_prebuilt"` requires the process command line to contain the regex. If you start via `bash -c "uv run ... server_prebuilt"` (hermes background), the **bash** process's command line is the full bash invocation, not "server_prebuilt". The actual `python -m src.server_prebuilt` is a grandchild.

**Test pattern**:
```bash
ps -ef | grep "python.*server_prebuilt" | grep -v grep
```
The `python3 -m src.server_prebuilt` line will appear (not the bash wrapper). `killall -r "python.*server_prebuilt"` matches on the python3 line directly.

## Pitfall 4: Forgetting to verify all four endpoints after server change

When you change server code (e.g. add `/sessions/{id}/api/offer`), the **PrebuiltUI** client uses the `/sessions/...` path, not `/api/offer`. If you only test the bare `/api/offer` endpoint with curl, you'll miss the 404 the browser is actually hitting.

**Verification checklist** (run all four after every server change):
```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:8766/         # 307
curl -sS -X POST http://localhost:8766/start -d '{}' -m 5               # 200
curl -sS -X POST http://localhost:8766/api/offer -d '{}' -m 5           # 200
curl -sS -X POST http://localhost:8766/sessions/TEST/api/offer -d '{}' -m 5  # 200
```

## Pitfall 5: The server.log file is rotated/stale when you switch launchers

When hermes session and user terminal both have running servers, they write to the SAME log file (or different). After killing one, the other keeps writing — but the log file's mtime is the most recent write, so `stat -c '%Y'` doesn't tell you which process wrote the most recent line.

**Diagnostic**:
```bash
# Show process holding the log file fd:
lsof /home/dr/workspace/pipecat/.cache-uv/server.log

# Or: check if a process is still writing (file size + mtime should keep updating):
ls -la /home/dr/workspace/pipecat/.cache-uv/server.log
# Watch with: watch -n1 'ls -la .../server.log'
```

If the file size stops growing, the writer is dead — your new server is writing to a different location.
