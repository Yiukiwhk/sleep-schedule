"""Version identifiers shared by the API, the desktop shell and the updater.

Keep this file ASCII-only: it is imported very early (before logging is set up)
and is also read by build tooling on machines whose console codepage is not UTF-8.
"""

VERSION = "0.1.1"

# Oldest desktop build that may talk to this server.  The updater manifest on the
# site (desktop/latest.json) carries the same value; a client below it gets
# sync.client_too_old instead of half-working sync.
MIN_SUPPORTED = "0.1.0"

# Bumped whenever the local/remote table layout changes in a way that needs a
# migration the client has to know about.
SCHEMA_VERSION = 1
