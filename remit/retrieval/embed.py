"""Embeddings, and an honest label for which kind you got.

REMIT retrieves products by meaning as well as by words. Two embedders are
implemented behind one protocol, and the system reports which one actually ran
rather than letting "semantic search" imply a neural model that is not there:

  HashingEmbedder        always available. Character 3-to-5-grams and word
                         uni/bigrams hashed into a fixed vector, sublinear term
                         frequency, L2 normalised. This is lexical-semantic:
                         it generalises over spelling, word order and
                         morphology, and it does NOT know that a slide is a
                         kind of sandal. It is deterministic, needs no model
                         file, and runs in microseconds on a 512 MB instance.

  SentenceEmbedder       used when `sentence-transformers` and a local model
                         are present. Real dense semantics. Not available on
                         the free-tier deployment, and the API says so instead
                         of pretending.

Both are pure functions of their input, so a retrieval result is reproducible
from the catalog version alone -- which is what lets the evaluation replay a
journey months later and get the same candidates.
"""
from __future__ import annotations

import math
import re
from typing import Protocol

_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dim: int
    name: str
    kind: str          # 'lexical-semantic' | 'dense-neural'

    def embed(self, text: str) -> list[float]: ...


def cosine(a: list[float], b: list[float]) -> float:
    """Both vectors arrive L2-normalised, so this is just a dot product.
    Kept as a named function anyway: an unnormalised vector reaching here is a
    bug worth being able to see in a profile."""
    return sum(x * y for x, y in zip(a, b))


class HashingEmbedder:
    """The always-available embedder.

    Feature hashing rather than a learned vocabulary, because a learned
    vocabulary has to be rebuilt when the catalog changes and then the old
    vectors mean something different. A hash is stable forever, which matters
    when a stored decision has to be replayable.

    The signed-hash trick (each feature contributes +1 or -1 by a second hash)
    keeps collisions from systematically inflating similarity: two unrelated
    features landing in the same bucket cancel as often as they add.
    """

    dim = 512
    name = "hashing-charngram-512"
    kind = "lexical-semantic"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        text = text.lower()
        words = _TOKEN.findall(text)
        feats: list[str] = []
        for w in words:
            feats.append("w:" + w)
            if len(w) > 4:                       # a crude stem, for morphology
                feats.append("s:" + w[:-1])
        for i in range(len(words) - 1):
            feats.append("b:" + words[i] + "_" + words[i + 1])
        flat = " ".join(words)
        for n in (3, 4, 5):
            for i in range(len(flat) - n + 1):
                gram = flat[i:i + n]
                if " " not in gram:
                    feats.append(f"c{n}:" + gram)
        return feats

    def embed(self, text: str) -> list[float]:
        counts: dict[int, float] = {}
        for f in self._features(text):
            h = hash_str(f)
            idx = h % self.dim
            sign = 1.0 if (h >> 16) & 1 else -1.0
            counts[idx] = counts.get(idx, 0.0) + sign
        # Sublinear scaling: a word repeated five times is not five times as
        # much evidence, and product names repeat brand tokens constantly.
        vec = [0.0] * self.dim
        for i, v in counts.items():
            vec[i] = math.copysign(1.0 + math.log(abs(v)), v) if v else 0.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec


def hash_str(s: str) -> int:
    """FNV-1a. Python's built-in hash() is salted per process, which would make
    every restart produce different vectors and quietly break replay."""
    h = 0xcbf29ce484222325
    for ch in s.encode():
        h ^= ch
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


class SentenceEmbedder:
    """Dense neural embeddings, when the hardware and the package allow.

    Deliberately not a hard dependency: this project has to run and be
    evaluated on a machine with no model files and no network, and a retrieval
    layer that only works on a laptop with 8 GB free is not a retrieval layer.
    """

    kind = "dense-neural"

    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer   # noqa: F401
        self._m = SentenceTransformer(model)
        self.name = model
        self.dim = int(self._m.get_sentence_embedding_dimension())

    def embed(self, text: str) -> list[float]:
        v = self._m.encode(text, normalize_embeddings=True)
        return [float(x) for x in v]


_RESOLVED: dict[str, Embedder] = {}


def best_available(prefer_neural: bool = True) -> Embedder:
    """The neural one if it is genuinely there, the hashing one otherwise.

    No warning, no degraded-mode banner, no exception -- the caller asks the
    embedder what it is and reports that. Silent substitution is only a problem
    when the substitution is not visible, and here it is on the API surface.
    """
    key = "neural" if prefer_neural else "hashing"
    if key in _RESOLVED:
        return _RESOLVED[key]
    e: Embedder = HashingEmbedder()
    if prefer_neural:
        try:
            e = SentenceEmbedder()
        except Exception:
            # Resolved ONCE per process. Importing sentence_transformers costs
            # seconds and reaching for a model that is not there costs a network
            # timeout; the evaluation builds 540 apps, and paying either of
            # those 540 times took the suite from 13 seconds to over four
            # minutes. A negative answer is an answer worth caching.
            pass
    _RESOLVED[key] = e
    return e
