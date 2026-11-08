from FlagEmbedding import BGEM3FlagModel
from qdrant_client import models

from worker import config


class BgeM3Embedder:
    """BGE-M3 gives both vectors hybrid search needs from one model: dense (meaning) and sparse (keywords)."""

    def __init__(self):
        self.model = BGEM3FlagModel(
            config.EMBEDDING_MODEL_ID,
            use_fp16=False,
            cache_dir=str(config.MODEL_CACHE_DIRECTORY),
        )

    def embed(self, texts):
        encoded = self.model.encode(texts, return_dense=True, return_sparse=True, return_colbert_vecs=False)
        embeddings = []
        for position in range(len(texts)):
            dense_vector = encoded["dense_vecs"][position].tolist()
            sparse_vector = self.to_sparse_vector(encoded["lexical_weights"][position])
            embeddings.append(TextEmbedding(dense_vector, sparse_vector))
        return embeddings

    def to_sparse_vector(self, lexical_weights):
        indices = []
        values = []
        for token_id, weight in lexical_weights.items():
            indices.append(int(token_id))
            values.append(float(weight))
        return models.SparseVector(indices=indices, values=values)


class TextEmbedding:
    def __init__(self, dense_vector, sparse_vector):
        self.dense_vector = dense_vector
        self.sparse_vector = sparse_vector
