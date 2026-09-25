"""Offline reference tokenization; preparing the public vocabulary is an explicit action."""

import base64
import hashlib
from pathlib import Path
from urllib.request import urlopen

import tiktoken

from ...errors import OwlError
from ...measurement.contracts import Exposure
from ...serialization import atomic_text

URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
DIGEST = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
PATTERN = r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"


class LocalCounter:
    def __init__(self, root: Path) -> None:
        self.path = root / "reference-tokenizer.tiktoken"
        self.encoding: tiktoken.Encoding | None = None

    def prepare(self) -> Exposure:
        with urlopen(URL, timeout=20) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000 or hashlib.sha256(raw).hexdigest() != DIGEST:
            raise OwlError("TOKENIZER_INTEGRITY", "Reference vocabulary failed integrity validation")
        atomic_text(self.path, raw.decode("ascii"))
        return self.count(("Reference tokenizer ready",))

    def count(self, texts: tuple[str, ...]) -> Exposure:
        if self.encoding is None and self.path.is_file():
            with self.path.open("rb") as stream:
                raw = stream.read(2_000_001)
            if hashlib.sha256(raw).hexdigest() != DIGEST:
                raise OwlError("TOKENIZER_INTEGRITY", "Reference vocabulary failed integrity validation")
            ranks = {
                base64.b64decode(token): int(rank)
                for token, rank in (line.split() for line in raw.splitlines())
            }
            self.encoding = tiktoken.Encoding(
                name="owlmatic_cl100k_reference", pat_str=PATTERN, mergeable_ranks=ranks, special_tokens={}
            )
        return Exposure(
            visible_bytes=sum(len(text.encode()) for text in texts),
            tool_results=len(texts),
            reference_tokens=sum(len(self.encoding.encode_ordinary(text)) for text in texts)
            if self.encoding
            else None,
            counter="cl100k_base_reference" if self.encoding else "bytes",
        )
