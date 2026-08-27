from functools import lru_cache

from pydantic_ai import Embedder
from pydantic_ai.embeddings.sentence_transformers import SentenceTransformerEmbeddingModel

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def load_embedding_model() -> Embedder:
    """Build the embedder once per process. instrument=True makes embedding calls show up in
    Logfire traces like every other model call in this repo; the underlying SentenceTransformer
    weights are still downloaded/loaded lazily on first embed() call, not here."""
    return Embedder(SentenceTransformerEmbeddingModel(EMBEDDING_MODEL_NAME), instrument=True)


async def encode_texts(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    """Encode a batch of documents to embedding vectors, as plain lists of floats (asyncpg's
    pgvector codec accepts a plain list; it doesn't need a numpy array)."""
    result = await embedder.embed_documents(texts)
    return [list(vector) for vector in result.embeddings]


async def encode_text(embedder: Embedder, text: str) -> list[float]:
    """Encode a single search query. Uses embed_query rather than embed_documents — a no-op
    distinction for all-MiniLM-L6-v2, but correct if this ever moves to a model with
    asymmetric query/document prompts."""
    result = await embedder.embed_query(text)
    return list(result.embeddings[0])
