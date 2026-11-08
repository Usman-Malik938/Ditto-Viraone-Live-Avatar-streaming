from qdrant_client import models

from worker import config

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"


class QdrantKnowledgeIndex:
    """One language's collection in embedded Qdrant. Search = dense + sparse results merged with Reciprocal Rank Fusion."""

    def __init__(self, client, collection_name):
        self.client = client
        self.collection_name = collection_name

    def rebuild(self, chunks, embeddings):
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)
        self.client.create_collection(
            self.collection_name,
            vectors_config={DENSE_VECTOR_NAME: models.VectorParams(size=config.EMBEDDING_DIMENSIONS, distance=models.Distance.COSINE)},
            sparse_vectors_config={SPARSE_VECTOR_NAME: models.SparseVectorParams()},
        )
        # Embedded Qdrant on Windows can keep the old storage file through delete_collection, so chunks removed from the
        # knowledge file would stay searchable. Emptying the collection explicitly guarantees a clean index.
        self.client.delete(self.collection_name, points_selector=models.FilterSelector(filter=models.Filter()))
        points = []
        for position in range(len(chunks)):
            chunk = chunks[position]
            embedding = embeddings[position]
            points.append(models.PointStruct(
                id=chunk.chunk_id,
                vector={DENSE_VECTOR_NAME: embedding.dense_vector, SPARSE_VECTOR_NAME: embedding.sparse_vector},
                payload={"section": chunk.section, "heading": chunk.heading, "text": chunk.text},
            ))
        self.client.upsert(self.collection_name, points=points)

    def exists(self):
        return self.client.collection_exists(self.collection_name)

    def best_dense_score(self, query_embedding):
        response = self.client.query_points(
            self.collection_name,
            query=query_embedding.dense_vector,
            using=DENSE_VECTOR_NAME,
            limit=1,
        )
        if not response.points:
            return 0.0
        return response.points[0].score

    def hybrid_search(self, query_embedding, limit):
        response = self.client.query_points(
            self.collection_name,
            prefetch=[
                models.Prefetch(query=query_embedding.dense_vector, using=DENSE_VECTOR_NAME, limit=config.RETRIEVAL_CANDIDATES_PER_SEARCH),
                models.Prefetch(query=query_embedding.sparse_vector, using=SPARSE_VECTOR_NAME, limit=config.RETRIEVAL_CANDIDATES_PER_SEARCH),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
        )
        passages = []
        for point in response.points:
            passages.append(KnowledgePassage(point.payload["section"], point.payload["heading"], point.payload["text"], point.score))
        return passages


class KnowledgePassage:
    def __init__(self, section, heading, text, fusion_score):
        self.section = section
        self.heading = heading
        self.text = text
        self.fusion_score = fusion_score

    def as_dictionary(self):
        return {"section": self.section, "heading": self.heading, "text": self.text, "fusion_score": round(self.fusion_score, 4)}

    def as_prompt_text(self):
        return f"[{self.section} > {self.heading}]\n{self.text}"
