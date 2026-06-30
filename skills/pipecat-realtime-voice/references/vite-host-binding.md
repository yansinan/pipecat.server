# Vite Dev Server Host Binding for Cross-Machine Access

By default `npm run dev` listens on `localhost` (`[::1]:5173`). From another
machine (e.g. helix via Tailscale), the page is unreachable.

## Fix: vite.config.js

```javascript
import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
});
```

Or use `--host` flag:
```bash
npx vite --host 0.0.0.0
```

## Verification

After starting with `0.0.0.0`, the Vite startup log shows:
```
➜  Local:   http://localhost:5173/
➜  Network: http://192.168.1.249:5173/
➜  Network: http://100.66.66.249:5173/   ← accessible from Tailscale peers
```

## Multiple Vite Instances

If you kill and restart Vite, old instances on different ports (5173, 5174)
may linger. Check with:
```bash
ss -tlnp | grep 517
```
