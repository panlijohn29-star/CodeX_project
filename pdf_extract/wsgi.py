"""WSGI adapter that lets the Flask platform serve the PDF extraction FastAPI app."""
import threading

_lock = threading.Lock()
_wsgi_app = None


def get_wsgi_app():
    # Imported lazily: loading the backend creates the data directory and database.
    global _wsgi_app
    with _lock:
        if _wsgi_app is None:
            from a2wsgi import ASGIMiddleware

            from .backend.main import app

            _wsgi_app = ASGIMiddleware(app)
        return _wsgi_app
