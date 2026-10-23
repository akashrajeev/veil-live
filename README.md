# Veil - live demo

Hosted demo of Veil, a conversation-level disclosure guard for shared assistants (Amazon Developer Hackathon, Alexa+ track).
Source and validation artifacts: https://github.com/akashrajeev/veil

This repo only adds a small per-visitor gateway (`gateway.py`) so the real, unmodified Veil server can be hosted publicly. Each browser gets its own private Veil process and state, so visitors cannot see or change each other's runs. Idle sessions stop after 15 minutes.

- `/` guarded vs unguarded comparison
- `/alexa` simulated assistant talking to the real MCP server
- `/real` runs on real public calendar feeds

Synthetic model, not a real privacy guarantee. No Amazon service or device is contacted.

Run locally: `python3 server.py`. Run the hosted shape: `python3 gateway.py`.
