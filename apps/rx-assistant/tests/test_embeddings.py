from types import SimpleNamespace

from rx_assistant.embeddings import encode_text, encode_texts


class FakeEmbedder:
    async def embed_documents(self, texts):
        return SimpleNamespace(embeddings=[[float(i), float(len(t))] for i, t in enumerate(texts)])

    async def embed_query(self, text):
        return SimpleNamespace(embeddings=[[0.0, float(len(text))]])


async def test_encode_texts_returns_list_of_float_lists() -> None:
    embedder = FakeEmbedder()

    vectors = await encode_texts(embedder, ["a", "bb", "ccc"])

    assert vectors == [[0.0, 1.0], [1.0, 2.0], [2.0, 3.0]]


async def test_encode_text_returns_single_vector() -> None:
    embedder = FakeEmbedder()

    vector = await encode_text(embedder, "hello")

    assert vector == [0.0, 5.0]
