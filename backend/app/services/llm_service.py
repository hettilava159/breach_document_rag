import json
import logging
from typing import List, Dict, Any, AsyncGenerator
from langchain_core.messages import HumanMessage
from app.config import settings

logger = logging.getLogger("app.llm_service")

SYSTEM_PROMPT = """You are Sara, a highly professional, expert Document Q&A assistant. 
Your task is to answer the user's question using the provided text chunks extracted from an uploaded document.

Instructions:
1. Base your answer on the provided Context. You may make reasonable inferences, summarize, and synthesize the information presented.
2. Do not fabricate facts. If the context does not contain any information related to the question, state that you cannot find the answer in the document.
3. Be helpful, clear, and comprehensive. Structure your answer logically to provide a complete response.
4. Always cite the page number(s) (e.g. "[Page X]") where the facts were found in the Context.
5. DO NOT use any Markdown formatting in your response (such as bolding via `**`, headers via `#`, or markdown bullet lists). The response must be strictly in simple plain text, as the frontend client only displays unformatted normal text.
"""

def build_prompt(query: str, chunks: List[Dict[str, Any]], history: List[Dict[str, str]] = None) -> str:
    """
    Combines search chunks, conversation history, and user query into a structured context prompt.
    """
    context_str = ""
    for idx, chunk in enumerate(chunks):
        page_num = chunk.get("page_number", "Unknown")
        chunk_text = chunk.get("text", "")
        chunk_context = chunk.get("context", "")
        
        context_str += f"--- Chunk {idx + 1} (Page {page_num}) ---\n"
        if chunk_context:
            context_str += f"[Document Context]: {chunk_context}\n"
        context_str += f"[Chunk Content]: {chunk_text}\n\n"

    # Format the last 4 messages of history to avoid token size bloat.
    # Wrapped in explicit turn tags (rather than bare "User:"/"Assistant:" labels) so
    # a message containing text like "Assistant:" can't be mistaken by the model for
    # an actual role switch/new instruction.
    history_str = ""
    if history:
        for msg in history[-4:]:
            role = "user" if msg.get("sender") == "user" else "assistant"
            text = msg.get("text", "")
            if text:
                history_str += f"<{role}_turn>{text}</{role}_turn>\n"

    prompt = f"""{SYSTEM_PROMPT}

Context Chunks:
{context_str}
"""
    if history_str:
        prompt += f"""
Conversation History:
{history_str}
"""

    prompt += f"""
User Question: {query}

Helpful Answer:"""
    return prompt


def get_llm_client(temperature: float = 0.1, json_mode: bool = False):
    """
    Helper to resolve the LangChain LLM chat client depending on settings.
    Supports OpenRouter, Groq, and Ollama.

    json_mode requests native structured/JSON output from providers that support it,
    which is far more reliable than asking the model to emit JSON in prose and
    scraping braces out of the response text.
    """
    if settings.LLM_PROVIDER == "openrouter":
        if not settings.OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY is not configured in settings.")
        from langchain_openai import ChatOpenAI
        kwargs = {}
        if json_mode:
            kwargs["model_kwargs"] = {"response_format": {"type": "json_object"}}
        return ChatOpenAI(
            openai_api_key=settings.OPENROUTER_API_KEY,
            openai_api_base="https://openrouter.ai/api/v1",
            model_name=settings.OPENROUTER_MODEL or "meta-llama/llama-3-8b-instruct:free",
            temperature=temperature,
            default_headers={
                "HTTP-Referer": "https://github.com/PRANAVMANDANI/rag-pdf-contextual-retrieval",
                "X-Title": "AskPDF RAG Engine"
            },
            **kwargs
        )
    elif settings.LLM_PROVIDER == "groq":
        if not settings.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY is not configured in settings.")
        from langchain_groq import ChatGroq
        kwargs = {}
        if json_mode:
            kwargs["model_kwargs"] = {"response_format": {"type": "json_object"}}
        return ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model="llama-3.3-70b-versatile",
            temperature=temperature,
            **kwargs
        )
    elif settings.LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.OLLAMA_MODEL,
            temperature=temperature,
            num_ctx=16384,
            format="json" if json_mode else None
        )
    else:
        raise ValueError(f"Unsupported LLM provider: {settings.LLM_PROVIDER}")


def get_structured_llm_client(schema, temperature: float = 0.1):
    """
    Returns an LLM client bound to a Pydantic schema via LangChain's structured
    output support. The provider's JSON response is parsed and validated
    against `schema` before it's ever handed back to the caller - a malformed
    or incomplete response raises instead of silently returning an unvalidated
    dict, unlike hand-rolled JSON scraping.
    """
    base_client = get_llm_client(temperature=temperature, json_mode=False)
    return base_client.with_structured_output(schema, method="json_mode")


async def generate_document_summary(whole_document: str) -> str:
    """
    Generates a brief high-level summary of the overall document using the selected provider.
    This summary is prepended to individual chunks to bypass token-per-minute rate limits.
    """
    if settings.LLM_PROVIDER == "openrouter" and not settings.OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY is not defined. Skipping summary generation.")
        return "No document summary available."
    elif settings.LLM_PROVIDER == "groq" and not settings.GROQ_API_KEY:
        logger.warning("GROQ_API_KEY is not defined. Skipping summary generation.")
        return "No document summary available."
    
    # Truncate whole_document to first ~30,000 characters to protect rate limits and context window
    truncated_doc = whole_document[:30000]
    
    prompt = f"""You are a document analyzer. Read the following text from a document and generate a brief, high-level summary (1-2 sentences) of the document's main subject, title/name (if identifiable), author/entity, and core purpose.
    
Document text:
{truncated_doc}
 
Respond with ONLY the 1-2 sentence summary, direct and clear, without any introductory words or formatting."""

    try:
        chat = get_llm_client(temperature=0.1)
        response = await chat.ainvoke([HumanMessage(content=prompt)])
        return response.content.strip()
    except Exception as e:
        logger.error(f"Error generating document summary via LangChain: {e}")
        return "Document containing technical text."


def build_chunk_context(document_summary: str, category: str, clause_reference: str) -> str:
    """
    Deterministically situates a chunk within the document for embedding/retrieval purposes.
    Structured extraction already tags every chunk with a category and clause reference,
    so an LLM call per chunk to describe the same thing would be pure spend for no new
    information — this builds an equivalent context string from that metadata for free.
    """
    ref_part = f" (Clause {clause_reference})" if clause_reference and clause_reference != "N/A" else ""
    label = f"{category} clause{ref_part}" if category and category != "General" else f"Excerpt{ref_part}"
    return f"{label} from: {document_summary}"


async def generate_response_stream(
    query: str, 
    chunks: List[Dict[str, Any]], 
    history: List[Dict[str, str]] = None
) -> AsyncGenerator[str, None]:
    """
    Async generator that streams the LLM-generated answer text word-by-word via LangChain.
    """
    prompt = build_prompt(query, chunks, history)

    try:
        chat = get_llm_client(temperature=0.1)
        is_first_chunk = True
        accumulated_leading = ""
        
        async for chunk in chat.astream([HumanMessage(content=prompt)]):
            if chunk.content:
                # Remove markdown formatting characters to keep text plain and clean
                content = chunk.content.replace("**", "").replace("*", "").replace("#", "").replace("`", "").replace("__", "")
                
                # Strip leading whitespace/newlines at the start of the stream
                if is_first_chunk:
                    accumulated_leading += content
                    stripped = accumulated_leading.lstrip()
                    if stripped:
                        is_first_chunk = False
                        yield stripped
                else:
                    yield content
    except Exception as e:
        logger.error(f"LangChain streaming failed: {e}")
        yield f"\n[Error generating response: {str(e)}]"


async def extract_metadata_from_text(first_page_text: str) -> Dict[str, Any]:
    """
    Uses LLM to extract title and author from the first page text of a document.
    """
    if not first_page_text:
        return {}
        
    prompt = f"""You are a document analyzer. Read the following text from the first page of a document and extract the Title and Author(s).
    
Text from first page:
{first_page_text[:2000]}

Respond ONLY in valid JSON format with keys "title" and "author". If they cannot be found, set the value to null.
Example response:
{{"title": "Document Title", "author": "John Doe"}}
Do not include any other text, markdown formatting, or explanation."""

    try:
        if settings.LLM_PROVIDER == "openrouter" and not settings.OPENROUTER_API_KEY:
            return {}
        elif settings.LLM_PROVIDER == "groq" and not settings.GROQ_API_KEY:
            return {}
            
        chat = get_llm_client(temperature=0.1)
        response = await chat.ainvoke([HumanMessage(content=prompt)])
        text_res = response.content.strip()
            
        if not text_res:
            return {}
            
        # Clean markdown code blocks if any
        if text_res.startswith("```"):
            lines = text_res.split("\n")
            if lines[0].startswith("```json"):
                text_res = "\n".join(lines[1:-1])
            elif lines[0].startswith("```"):
                text_res = "\n".join(lines[1:-1])
        
        data = json.loads(text_res)
        return {
            "title": data.get("title"),
            "author": data.get("author")
        }
    except Exception as e:
        logger.error(f"Error extracting metadata via LangChain: {e}")
        return {}


PAGES_PER_EXTRACTION_BATCH = 8


def _parse_json_response(text_res: str) -> Dict[str, Any]:
    """
    Parses a (hopefully) JSON LLM response, defensively stripping markdown
    code fences in case the provider doesn't fully honor json_mode.
    """
    text_res = text_res.strip()
    if text_res.startswith("```"):
        lines = text_res.split("\n")
        if lines[0].startswith("```json") or lines[0].startswith("```"):
            text_res = "\n".join(lines[1:-1])

    start_idx = text_res.find("{")
    end_idx = text_res.rfind("}")
    if start_idx != -1 and end_idx != -1:
        text_res = text_res[start_idx:end_idx + 1]

    return json.loads(text_res)


async def extract_clauses_from_pages(pages_batch: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    """
    Uses a single LLM call to perform structured clause extraction and classification
    across a batch of pages, tagging each extracted clause with its source page number.
    Requests native JSON-mode output rather than scraping JSON out of prose, which is
    far more reliable than free-text parsing.

    Returns a dict mapping page_number -> list of extracted clauses for that page.
    Pages that fail to parse or come back empty are simply absent/empty in the result;
    the caller is responsible for falling back to text splitting for those pages.
    """
    page_numbers = [p["page_number"] for p in pages_batch]
    empty_result = {pn: [] for pn in page_numbers}

    non_empty_pages = [p for p in pages_batch if p["text"]]
    if not non_empty_pages:
        return empty_result

    pages_block = "\n\n".join(
        f"=== PAGE {p['page_number']} ===\n{p['text']}" for p in non_empty_pages
    )

    prompt = f"""You are an expert legal contract compliance auditor.
Read the following text, taken from multiple pages of a document (each page is marked with "=== PAGE N ==="), and extract all legal clauses from EVERY page provided.
A legal clause is any numbered section, paragraph, or clear subsection (e.g. "Clause 2.2", "Section 5.1", "4. LIABILITY", "Confidentiality:", etc.) that establishes a legal duty, right, or liability.

For each clause, you must extract:
1. Page Number. The exact page number (the N from "=== PAGE N ===") that this clause was found on.
2. Clause Reference/Number (e.g. "2.2", "Section 4.1", "Clause 7", or "N/A" if it has no number/title).
3. Category. Choose exactly one of the following:
   - Confidentiality
   - Liability
   - Indemnification
   - Non-Compete
   - Non-Solicitation
   - IP Assignment
   - Termination
   - Governing Law
   - Arbitration
   - Renewal
   - General (if it's another standard clause or general text)
4. Exact Clause Text. Quote the exact text of the clause from the page. Do not summarize or paraphrase.

Respond ONLY in valid JSON format containing a list of objects.
Example response format:
{{
  "extracted_clauses": [
    {{
      "page_number": 3,
      "clause_reference": "2.2",
      "category": "Confidentiality",
      "exact_text": "The receiving party shall keep all information confidential..."
    }}
  ]
}}

Pages:
{pages_block}

Respond in the exact JSON format above. Do not include any other text, markdown formatting, or explanations."""

    async def run_extraction(json_mode: bool) -> Dict[str, Any]:
        chat = get_llm_client(temperature=0.1, json_mode=json_mode)
        response = await chat.ainvoke([HumanMessage(content=prompt)])
        return _parse_json_response(response.content)

    try:
        try:
            data = await run_extraction(json_mode=True)
        except Exception as json_mode_err:
            # Some providers/models reject or ignore response_format; retry once
            # in plain prompted-JSON mode before giving up on this batch entirely.
            logger.warning(
                f"JSON-mode extraction failed for pages {page_numbers}, retrying without forced JSON mode: {json_mode_err}"
            )
            data = await run_extraction(json_mode=False)

        extracted = data.get("extracted_clauses", [])

        result: Dict[int, List[Dict[str, Any]]] = {pn: [] for pn in page_numbers}
        valid_page_numbers = set(page_numbers)
        for item in extracted:
            pn = item.get("page_number")
            # Only trust page numbers that were actually part of this batch.
            if pn in valid_page_numbers:
                result[pn].append(item)
            else:
                logger.warning(f"Discarding clause with out-of-batch page_number {pn} (batch was {page_numbers})")
        return result
    except Exception as e:
        logger.error(f"Error extracting clauses for page batch {page_numbers}: {e}")
        return empty_result


async def extract_and_chunk_pdf_document(pages_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Runs structured clause extraction in batches of PAGES_PER_EXTRACTION_BATCH pages per
    LLM call (in parallel across batches), which cuts the number of LLM round trips by
    roughly that factor compared to one call per page.
    Falls back to RecursiveTextSplitter for individual pages where extraction fails or
    returns no chunks, guaranteeing that all page text is indexed.
    """
    import asyncio
    from app.services.pdf_service import RecursiveTextSplitter

    logger.info(f"Extracting structured clauses from {len(pages_data)} pages...")

    batches = [
        pages_data[i:i + PAGES_PER_EXTRACTION_BATCH]
        for i in range(0, len(pages_data), PAGES_PER_EXTRACTION_BATCH)
    ]

    # Run batch extraction in parallel with a semaphore to avoid rate limit spikes
    semaphore = asyncio.Semaphore(5)

    async def run_with_sem(batch: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
        async with semaphore:
            return await extract_clauses_from_pages(batch)

    tasks = [run_with_sem(batch) for batch in batches]
    batch_results = await asyncio.gather(*tasks)

    # Merge per-batch page->clauses maps into one lookup covering all pages
    clauses_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for batch_result in batch_results:
        clauses_by_page.update(batch_result)

    all_chunks = []
    splitter = RecursiveTextSplitter(chunk_size=600, chunk_overlap=150)

    for page in pages_data:
        page_num = page["page_number"]
        page_clauses = clauses_by_page.get(page_num, [])

        if page_clauses:
            for c_idx, c in enumerate(page_clauses):
                clause_text = c.get("exact_text", "").strip()
                if not clause_text:
                    continue

                ref = c.get("clause_reference", "N/A")
                cat = c.get("category", "General")

                # We save original exact_text in "text", and set category/clause_reference metadata
                all_chunks.append({
                    "text": clause_text,
                    "clause_reference": ref,
                    "category": cat,
                    "metadata": {
                        "page_number": page_num,
                        "chunk_index": len(all_chunks)
                    }
                })
        else:
            # Fallback for pages that failed to extract structured clauses
            logger.warning(f"Failed to extract structured clauses on page {page_num}. Falling back to text splitting.")
            split_chunks = splitter.split_text(page["text"])
            for split_idx, chunk_text in enumerate(split_chunks):
                all_chunks.append({
                    "text": chunk_text,
                    "clause_reference": "N/A",
                    "category": "General",
                    "metadata": {
                        "page_number": page_num,
                        "chunk_index": len(all_chunks)
                    }
                })

    logger.info(f"Structured extraction produced {len(all_chunks)} chunks for database storage.")
    return all_chunks

