"""Data-access objects used by `app.routes` for external services.

Keeping IO-heavy adapters (Gemini Vision, PyMuPDF extraction) in this package
isolates retry / fallback logic from the HTTP layer so routes stay small.
"""
