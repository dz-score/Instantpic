"""HTTP surface, one module per domain.

Routers receive their services through backend.deps (settings, printer, camera),
which reads what main.py's lifespan built onto app.state — they never import a
service singleton to reach it, and never import backend.main.
"""
