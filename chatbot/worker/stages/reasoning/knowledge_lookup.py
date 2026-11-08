import threading

from qdrant_client import QdrantClient

from worker import config
from worker.stages.reasoning.knowledge_chunker import MarkdownSectionChunker
from worker.stages.reasoning.knowledge_index import QdrantKnowledgeIndex
from worker.stages.reasoning.text_embedder import BgeM3Embedder


class RetrievalBackend:
    """The embedding model and Qdrant client, shared by every language: the model takes seconds to load, embedded
    Qdrant allows one client per folder, and neither is safe to call from two threads at once."""

    def __init__(self):
        self.embedder = BgeM3Embedder()
        self.qdrant_client = QdrantClient(path=str(config.QDRANT_STORAGE_PATH))
        self.search_lock = threading.Lock()


class KnowledgeLookup:
    """RAG for one language: indexes its knowledge base and finds the passages for a question. Must stay under ~100 ms."""

    def __init__(self, retrieval_backend, language):
        self.retrieval_backend = retrieval_backend
        self.language = language
        self.index = QdrantKnowledgeIndex(retrieval_backend.qdrant_client, language.knowledge_collection_name)

    def rebuild_index(self):
        markdown_text = self.language.knowledge_base_path.read_text(encoding="utf-8")
        chunks = MarkdownSectionChunker().split(markdown_text)
        texts = []
        for chunk in chunks:
            texts.append(chunk.text_for_embedding())
        with self.retrieval_backend.search_lock:
            embeddings = self.retrieval_backend.embedder.embed(texts)
            self.index.rebuild(chunks, embeddings)
        return chunks

    def is_indexed(self):
        return self.index.exists()

    def find_relevant_passages(self, current_message, search_query, answers_avatar_question=False):
        """Searches with search_query (current + previous message, so follow-ups work), but returns nothing when the
        current message alone is off-topic; otherwise an earlier topic leaks into unrelated questions. An answer to the
        avatar's own question ("new", "5 pages") is exempt: it is short on purpose and belongs to the earlier topic."""
        search_result = self.search(current_message, search_query)
        if not search_result.passes_thresholds(self.language, answers_avatar_question):
            return []
        return search_result.ranked_passages

    def search(self, current_message, search_query):
        """The ranked passages before any threshold cut, with the scores the thresholds are checked against."""
        with self.retrieval_backend.search_lock:
            embeddings = self.retrieval_backend.embedder.embed([current_message, search_query])
            current_embedding = embeddings[0]
            search_embedding = embeddings[1]
            return SearchResult(
                self.index.best_dense_score(current_embedding),
                self.index.best_dense_score(search_embedding),
                self.index.hybrid_search(search_embedding, config.RETRIEVAL_TOP_K),
            )

    def build_search_query(self, previous_user_message, user_message, avatar_question=""):
        lines = []
        for line in (previous_user_message, avatar_question, user_message):
            if line:
                lines.append(line)
        return "\n".join(lines)


class SearchResult:
    def __init__(self, current_message_score, search_query_score, ranked_passages):
        self.current_message_score = current_message_score
        self.search_query_score = search_query_score
        self.ranked_passages = ranked_passages

    def passes_thresholds(self, language, answers_avatar_question=False):
        if not answers_avatar_question and self.current_message_score < language.retrieval_min_current_message_score:
            return False
        return self.search_query_score >= language.retrieval_min_dense_score
