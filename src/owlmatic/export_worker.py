"""Private observer entrypoint; no network work runs in a workflow subprocess."""

import os
import sys
from contextlib import suppress
from pathlib import Path

from .automatic_export import ExportWatcher
from .bootstrap import create_application
from .failures import public_failure
from .infrastructure.export_store import FileExportStore
from .infrastructure.system import SystemClock


def main() -> None:
    os.umask(0o077)
    root = Path(os.environ["OWLMATIC_HOME"])
    try:
        app = create_application(root)
        ExportWatcher(app.execution.runs, app.exports, SystemClock(), app.dashboard).watch(
            sys.argv[1], int(sys.argv[2])
        )
    except Exception as error:
        with suppress(Exception):
            FileExportStore(root).diagnostic(public_failure(error))


if __name__ == "__main__":
    main()
