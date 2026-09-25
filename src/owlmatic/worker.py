"""Internal entrypoint with bounded, safe diagnostics even when bootstrap fails."""

import os
import sys
from contextlib import suppress
from pathlib import Path

from .bootstrap import create_application
from .errors import OwlError
from .failures import public_failure
from .infrastructure.files import FileArtifacts


def main() -> None:
    os.umask(0o077)
    root = Path(os.environ.get("OWLMATIC_HOME", str(Path.home() / ".owlmatic"))).expanduser().resolve()
    run_id = sys.argv[1]
    try:
        application = create_application(root)
        job = application.execution.artifacts.job(run_id)
        application.supervisor.execute(job)
    except Exception as error:
        failure = public_failure(error)
        with suppress(OSError, OwlError):
            FileArtifacts(root).diagnostic(run_id, failure)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
