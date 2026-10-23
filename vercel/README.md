# Veil, hosted demo (Vercel)

Deployment source for https://veil-live.vercel.app. The Veil code in `app/` is the submitted project
(https://github.com/akashrajeev/veil) with these changes only:
- `server.py` renamed `veil_main.py`; operator token comes from `VEIL_OPERATOR_TOKEN`; MCP session ids are accepted if well-formed (no cross-request memory); `PORT`/`VEIL_BIND`/`VEIL_PUBLIC_HOSTS` env for the gateway.
- `mcp_server.py`: restores persisted approvals at the start of every request.
- `index.html` and `alexa.html`: hosting note; `index.html` restyled (same element IDs and behaviour).

`api/index.py` runs the unmodified server for one request and keeps each visitor's state files in an encrypted,
HMAC-authenticated cookie (`veil_state`, key from env `VEIL_SECRET`). Limits: a visitor can replay an older valid
cookie (rolls back their own session), simultaneous mutating requests can lose an update, cookie-less MCP clients
get fresh state per call. Not a production privacy guarantee. Synthetic model.

Deploy: `vercel deploy --prod -e VEIL_SECRET=<random>`.

## Rebuild this deployment from this repo
```
mkdir -p build/app build/api && cp -r $(ls -A | grep -v -E '^(vercel|\.git)$') build/app/
cd build/app && mv server.py veil_main.py && rm gateway.py && patch -p1 < ../../vercel/changes.patch && cd ..
cp ../vercel/api/index.py api/ && cp ../vercel/vercel.json . && echo > requirements.txt
vercel deploy --prod -e VEIL_SECRET=$(openssl rand -hex 24)
```
(run from the repo root; `changes.patch` is relative to the root files with `server.py` renamed to `veil_main.py`).
