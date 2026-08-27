import numpy as np

from rx_assistant.embeddings import encode_text, encode_texts


class FakeModel:
    def encode(self, texts, **kwargs):
        assert kwargs.get("convert_to_numpy") is True
        return np.array([[float(i), float(len(t))] for i, t in enumerate(texts)])


def test_encode_texts_returns_list_of_float_lists() -> None:
    model = FakeModel()

    vectors = encode_texts(model, ["a", "bb", "ccc"])

    assert vectors == [[0.0, 1.0], [1.0, 2.0], [2.0, 3.0]]


def test_encode_text_returns_single_vector() -> None:
    model = FakeModel()

    vector = encode_text(model, "hello")

    assert vector == [0.0, 5.0]
