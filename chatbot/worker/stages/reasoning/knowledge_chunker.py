from dataclasses import dataclass, field

SEARCH_PHRASES_MARKER = "> "


@dataclass
class KnowledgeChunk:
    chunk_id: int
    section: str
    heading: str
    text: str
    search_phrases: list = field(default_factory=list)

    def text_for_embedding(self):
        """The heading path goes in front so a chunk still makes sense on its own; the search phrases (typical customer
        wording) help matching but are never shown to the LLM."""
        lines = [f"{self.section} > {self.heading}"]
        lines.extend(self.search_phrases)
        lines.append(self.text)
        return "\n".join(lines)


class MarkdownSectionChunker:
    """One chunk per '###' heading, labelled with its '##' section. Text before the first '##' is a file note and skipped.
    Lines starting with '> ' are the chunk's search phrases."""

    def split(self, markdown_text):
        chunks = []
        current_section = ""
        current_heading = ""
        current_lines = []
        current_phrases = []

        for line in markdown_text.splitlines():
            if line.startswith("### "):
                self.append_chunk(chunks, current_section, current_heading, current_lines, current_phrases)
                current_heading = line[4:].strip()
                current_lines = []
                current_phrases = []
            elif line.startswith("## "):
                self.append_chunk(chunks, current_section, current_heading, current_lines, current_phrases)
                current_section = line[3:].strip()
                current_heading = ""
                current_lines = []
                current_phrases = []
            elif current_heading and line.startswith(SEARCH_PHRASES_MARKER):
                current_phrases.append(line[len(SEARCH_PHRASES_MARKER):].strip())
            elif current_heading:
                current_lines.append(line)

        self.append_chunk(chunks, current_section, current_heading, current_lines, current_phrases)
        return chunks

    def append_chunk(self, chunks, section, heading, lines, phrases):
        body = "\n".join(lines).strip()
        if not heading or not body:
            return
        chunks.append(KnowledgeChunk(chunk_id=len(chunks) + 1, section=section, heading=heading, text=body,
                                     search_phrases=phrases))
