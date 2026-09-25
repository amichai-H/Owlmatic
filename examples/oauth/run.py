"""Synthetic provider: rotation, durable storage, and old-token rejection."""

import json
import os
from pathlib import Path


class FakeProvider:
    def __init__(self) -> None:
        self.current = "fixture-refresh-0"
        self.generation = 0

    def refresh(self, token: str) -> str:
        if token != self.current:
            raise ValueError("invalid_grant")
        self.generation += 1
        self.current = f"fixture-refresh-{self.generation}"
        return self.current


def main() -> None:
    inputs = json.loads(Path(os.environ["OWLMATIC_INPUT"]).read_text())
    provider = FakeProvider()
    old = provider.current
    new = provider.refresh(old)
    storage = Path(os.environ["OWLMATIC_ARTIFACTS"]) / "synthetic-token.txt"
    storage.write_text(new if inputs["healthy"] else old)
    try:
        provider.refresh(old)
        rejected = False
    except ValueError:
        rejected = True
    persisted = storage.read_text() == new
    try:
        provider.refresh(storage.read_text())
        usable = True
    except ValueError:
        usable = False
    observations = {
        "token_rotated": new != old,
        "token_persisted": persisted,
        "old_token_rejected": rejected,
        "stored_token_usable": usable,
    }
    passed = all(observations.values())
    result = {
        "run_id": os.environ["OWLMATIC_RUN_ID"],
        "outcome": "pass" if passed else "fail",
        "checks": [{"name": name, "status": "pass" if ok else "fail"} for name, ok in observations.items()],
        "outputs": observations,
        "effects": "completed",
    }
    Path(os.environ["OWLMATIC_RESULT"]).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
