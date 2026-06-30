#!/bin/bash
# Scope-aware cleanup of server_prebuilt processes.
# Use this instead of `killall -9 python` which would also kill other
# Python services (e.g. bot.py on port 8765 owned by another session).

# Show what's running
echo "=== server_prebuilt processes ==="
ps -ef | grep "python.*server_prebuilt" | grep -v grep || echo "(none)"

echo ""
echo "=== Port 8766 listener ==="
ss -tlnp 2>/dev/null | grep 8766 || echo "(no listener)"

echo ""
echo "=== Killing all server_prebuilt ==="
killall -9 -r "python.*server_prebuilt" 2>/dev/null
sleep 2

echo ""
echo "=== After kill ==="
ps -ef | grep "python.*server_prebuilt" | grep -v grep || echo "(none — clean)"
ss -tlnp 2>/dev/null | grep 8766 || echo "(port 8766 free)"
