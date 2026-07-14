"""HTTP surface, one module per domain.

Routers import services (camera_provider, state_machine, print_svc, ...) but
never import backend.main — main.py assembles them onto the app.
"""
