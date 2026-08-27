from functools import lru_cache

from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def load_embedding_model() -> SentenceTransformer:
    """Load the local embedding model once per process. Never called from a test — it
    downloads real model weights on first use."""
    return SentenceTransformer(EMBEDDING_MODEL_NAME)


def encode_texts(model, texts: list[str]) -> list[list[float]]:
    """Encode a batch of texts to embedding vectors, as plain lists of floats (asyncpg's
    pgvector codec accepts a plain list; it doesn't need a numpy array)."""
    vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return [vector.tolist() for vector in vectors]


def encode_text(model, text: str) -> list[float]:
    return encode_texts(model, [text])[0]
