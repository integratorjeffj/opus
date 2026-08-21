"""The Opus app interface: a local server, a JSON API, and the dashboard.

Opus draws its interface with a browser but is not a web service. The server
binds to loopback, mints a per-run session token, and serves a UI that talks to
the same engine the command line does. Nothing is uploaded and nothing is
exposed to the network.
"""

from .server import serve            # noqa: F401

__all__ = ["serve"]
