import os

from app import create_app

app = create_app()

if __name__ == '__main__':
    # Debug reloader watches files and restarts the process on save — any in-flight HTTP
    # request (e.g. delete) can show "connection reset" / "Failed to fetch". Set
    # FLASK_NO_RELOADER=1 for a stable single process while testing UI flows.
    use_reloader = os.environ.get("FLASK_NO_RELOADER", "").lower() not in ("1", "true", "yes")
    # Debug defaults to OFF. This file is never invoked by the gunicorn/Procfile
    # production path, but explicitly gating it means running `python run.py`
    # against a misconfigured environment can't accidentally enable Werkzeug's
    # interactive debugger (arbitrary code execution risk) by default.
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in ("1", "true", "yes")
    # With debug off, Jinja caches compiled templates. Local `python run.py`
    # must still pick up HTML edits (LO <details>, Reports cards, etc.) without
    # requiring a full process restart every save.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True
    app.run(debug=debug, host="127.0.0.1", port=5000, use_reloader=use_reloader)