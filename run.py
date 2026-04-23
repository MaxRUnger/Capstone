import os

from app import create_app

app = create_app()

if __name__ == '__main__':
    # Debug reloader watches files and restarts the process on save — any in-flight HTTP
    # request (e.g. delete) can show "connection reset" / "Failed to fetch". Set
    # FLASK_NO_RELOADER=1 for a stable single process while testing UI flows.
    use_reloader = os.environ.get("FLASK_NO_RELOADER", "").lower() not in ("1", "true", "yes")
    app.run(debug=True, host="127.0.0.1", port=5000, use_reloader=use_reloader)