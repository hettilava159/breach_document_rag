import io
import logging
from typing import List, Dict, Any
from pypdf import PdfReader

logger = logging.getLogger("app.pdf_service")

class RecursiveTextSplitter:
    """
    Splits text recursively by looking at paragraph separators, sentence separators, 
    and space separators to keep semantic blocks together.
    """
    def __init__(self, chunk_size: int = 600, chunk_overlap: int = 150):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = ["\n\n", "\n", " ", ""]

    def split_text(self, text: str) -> List[str]:
        return self._split(text, self.separators)

    def _split(self, text: str, separators: List[str]) -> List[str]:
        # If the block is small enough, it's a chunk
        if len(text) <= self.chunk_size:
            return [text]

        # If no separators left, do a hard character-level cut
        if not separators:
            return [text[i:i + self.chunk_size] for i in range(0, len(text), self.chunk_size - self.chunk_overlap)]

        separator = separators[0]
        splits = text.split(separator) if separator else list(text)
        
        chunks = []
        current_chunk = ""

        for part in splits:
            # Restore the separator character except for character-level splits
            part_with_sep = part + (separator if separator else "")
            
            # If a single part exceeds the chunk size, we must recursively split it
            if len(part_with_sep) > self.chunk_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                
                # Split this huge part using the next (finer) separator
                deep_chunks = self._split(part, separators[1:])
                chunks.extend(deep_chunks)
            elif len(current_chunk) + len(part_with_sep) <= self.chunk_size:
                # Accumulate text into our current chunk
                current_chunk += part_with_sep
            else:
                # The current chunk is full, save it
                if current_chunk:
                    chunks.append(current_chunk.strip())
                
                # Start new chunk with overlap from the end of the previous chunk
                overlap_start = max(0, len(current_chunk) - self.chunk_overlap)
                overlap_text = current_chunk[overlap_start:]
                current_chunk = overlap_text + part_with_sep

        if current_chunk:
            chunks.append(current_chunk.strip())

        # Filter out empty chunks
        return [c for c in chunks if c.strip()]


def extract_pages_from_pdf(file_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Parses a PDF from byte stream and extracts raw text page-by-page.
    """
    try:
        # Load PDF bytes into a file-like memory stream
        pdf_file = io.BytesIO(file_bytes)
        reader = PdfReader(pdf_file)
        pages_data = []

        for page_idx, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            # Clean up excessive white spaces and weird formatting
            cleaned_text = " ".join(text.split())
            if cleaned_text:
                pages_data.append({
                    "page_number": page_idx + 1,
                    "text": cleaned_text
                })
        
        logger.info(f"Successfully extracted {len(pages_data)} pages from PDF.")
        return pages_data
    except Exception as e:
        logger.error(f"Error parsing PDF: {e}")
        raise ValueError(f"Failed to parse PDF: {str(e)}")


def chunk_pdf_document(pages_data: List[Dict[str, Any]], chunk_size: int = 600, chunk_overlap: int = 150) -> List[Dict[str, Any]]:
    """
    Takes extracted pages and chunks their text while preserving page number metadata.
    """
    splitter = RecursiveTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    all_chunks = []

    for page in pages_data:
        page_num = page["page_number"]
        page_text = page["text"]
        
        # Split text on this page into smaller semantic chunks
        split_chunks = splitter.split_text(page_text)
        
        for idx, chunk_text in enumerate(split_chunks):
            all_chunks.append({
                "text": chunk_text,
                "metadata": {
                    "page_number": page_num,
                    "chunk_index": idx
                }
            })
            
    logger.info(f"Split {len(pages_data)} pages into {len(all_chunks)} chunks.")
    return all_chunks


def extract_metadata_from_pdf(file_bytes: bytes) -> Dict[str, Any]:
    """
    Extracts PDF document metadata (author, title, etc.) if available.
    """
    try:
        pdf_file = io.BytesIO(file_bytes)
        reader = PdfReader(pdf_file)
        meta = reader.metadata
        
        extracted = {"author": None, "title": None}
        if meta:
            # Check pypdf attributes first, fallback to raw keys
            title = meta.title or meta.get("/Title")
            author = meta.author or meta.get("/Author")
            
            # Clean string values if they exist
            if title and isinstance(title, str):
                cleaned_title = title.strip()
                # Exclude default placeholder/meaningless titles
                if cleaned_title and not any(x in cleaned_title.lower() for x in ["untitled", "microsoft word", "document"]):
                    extracted["title"] = cleaned_title
            
            if author and isinstance(author, str):
                cleaned_author = author.strip()
                if cleaned_author:
                    extracted["author"] = cleaned_author
            
        return extracted
    except Exception as e:
        logger.error(f"Error extracting PDF metadata: {e}")
        return {"author": None, "title": None}
