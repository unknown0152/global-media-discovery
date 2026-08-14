"""Gunicorn WSGI entrypoint."""

from gmd.api import ReadOnlyAPI
from gmd.config import load_settings
from gmd.log import configure_logging
from gmd.ui import ReadOnlyUI

_settings = load_settings()
configure_logging(_settings.log_level)
_api = ReadOnlyAPI(_settings)
_ui = ReadOnlyUI(_settings)


def application(environ, start_response):
    """Dispatch JSON API and read-only HTMX fragment requests."""
    path = str(environ.get("PATH_INFO", "/"))
    if path.startswith("/ui/"):
        return _ui(environ, start_response)
    return _api(environ, start_response)
