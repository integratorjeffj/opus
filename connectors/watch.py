"""Watched folder: drop an export in, and a batch gets planned.

Polling rather than filesystem events, on purpose. The folder being watched is
very often a synced one -- Dropbox, Drive, OneDrive -- and those emit events
that do not mean what they appear to: a file materialises at zero bytes and
fills in afterwards, or is written to a temporary name and swapped. Polling for
a file whose size has stopped changing sidesteps the entire class of problem,
and a sheet-music publisher's orders do not need sub-second latency.

Nothing here stamps anything. A watcher's job ends at "there is a new export
and here is the plan it produces"; a human still approves it. That is the
Stage 2 boundary, and moving it is a later decision made on evidence.
"""

import time
from datetime import datetime
from pathlib import Path

SETTLE_SECONDS = 3.0
POLL_SECONDS = 10.0
STATE_FILE = ".opus-seen"


class WatchedFolder:
    """Watches a folder for new files matching a pattern.

    Remembers what it has already reported in a small state file, so a restart
    does not reprocess the morning's exports.
    """

    def __init__(self, folder, pattern="*.csv", state_file=None,
                 settle_seconds=SETTLE_SECONDS):
        self.folder = Path(folder)
        self.pattern = pattern
        self.settle_seconds = settle_seconds
        self.state_path = Path(state_file) if state_file else self.folder / STATE_FILE
        self._seen = self._load_seen()

    # -- state --------------------------------------------------------------

    def _load_seen(self):
        try:
            lines = self.state_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return set()
        return {line.strip() for line in lines if line.strip()}

    def _remember(self, key):
        self._seen.add(key)
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with self.state_path.open("a", encoding="utf-8") as fh:
                fh.write(key + "\n")
        except OSError:
            # A read-only drop folder is a legitimate setup. Losing the memory
            # across restarts is survivable; the ledger still prevents any
            # order being issued twice.
            pass

    @staticmethod
    def _key(path):
        """Identify a file by name and size, not by path alone.

        A re-uploaded export with the same name but different contents is a new
        file and should be picked up.
        """
        try:
            return "{}:{}".format(path.name, path.stat().st_size)
        except OSError:
            return path.name

    # -- scanning -----------------------------------------------------------

    def _is_settled(self, path):
        """True when the file has stopped growing.

        Catches the half-written-file case that every sync client produces.
        """
        try:
            first = path.stat().st_size
        except OSError:
            return False
        time.sleep(self.settle_seconds)
        try:
            return path.stat().st_size == first and first > 0
        except OSError:
            return False

    def scan(self):
        """Return new, settled files, oldest first. Marks them as seen."""
        if not self.folder.is_dir():
            return []

        candidates = sorted(
            (p for p in self.folder.glob(self.pattern)
             if p.is_file() and not p.name.startswith(".")),
            key=lambda p: p.stat().st_mtime)

        found = []
        for path in candidates:
            if self._key(path) in self._seen:
                continue
            if not self._is_settled(path):
                continue          # still arriving; it will be caught next pass
            self._remember(self._key(path))
            found.append(path)
        return found

    def forget_all(self):
        """Clear the memory so everything in the folder looks new again."""
        self._seen = set()
        try:
            self.state_path.unlink()
        except OSError:
            pass


def watch(folder, on_file, pattern="*.csv", poll_seconds=POLL_SECONDS,
          once=False, log=print, stop=None):
    """Poll `folder`, calling on_file(path) for each new settled file.

    `stop` is an optional callable checked between passes, so a caller can end
    the loop without killing the process. `once` does a single pass, which is
    what a scheduled task wants -- cron every fifteen minutes is usually a
    better answer than a daemon.
    """
    watcher = WatchedFolder(folder, pattern=pattern)
    if log:
        log("Watching {} for {} (every {:.0f}s). Ctrl-C to stop."
            .format(watcher.folder, pattern, poll_seconds))

    while True:
        try:
            for path in watcher.scan():
                stamp = datetime.now().strftime("%H:%M:%S")
                if log:
                    log("[{}] new file: {}".format(stamp, path.name))
                try:
                    on_file(path)
                except Exception as exc:
                    # One bad export must not end the watch.
                    if log:
                        log("[{}] {} failed: {}".format(stamp, path.name, exc))
        except KeyboardInterrupt:
            if log:
                log("Stopped.")
            return 0

        if once or (stop and stop()):
            return 0
        try:
            time.sleep(poll_seconds)
        except KeyboardInterrupt:
            if log:
                log("Stopped.")
            return 0
