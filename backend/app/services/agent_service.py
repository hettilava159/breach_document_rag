import asyncio
import logging
import httpx
from typing import List, Dict, Any
from bson import ObjectId
from pydantic import BaseModel, Field
from app.config import settings
from app.database import Database
from app.services.llm_service import get_structured_llm_client
from langchain_core.messages import HumanMessage

logger = logging.getLogger("app.agent_service")

# Groq's on-demand tier caps llama-3.3-70b-versatile at 12,000 tokens/minute.
# These bounds keep the Stage 3 synthesis prompt (instructions + clause text +
# web search snippets) comfortably under that limit even for contracts that
# trigger all risk categories.
TAVILY_SNIPPET_MAX_CHARS = 250
MAX_CLAUSE_CHARS = 900
MAX_CONTEXT_CLAUSES = 25
MAX_SEARCH_QUERIES = 5
TOKEN_LIMIT_ERROR_MARKERS = ("413", "rate_limit_exceeded", "tokens per minute", "request too large")

# 1. Structured Output Schemas using Pydantic
class RiskItem(BaseModel):
    category: str = Field(description="Risk category (Termination, Liability, IP Ownership, Indemnity, Jurisdiction, Other)")
    clause_text: str = Field(description="Exact risk clause text extracted from the document")
    severity: str = Field(description="Severity level: HIGH, MEDIUM, or LOW")
    severity_color: str = Field(description="Color associated: red (for HIGH), yellow (for MEDIUM), green (for LOW)")
    explanation: str = Field(description="Why this clause is risky and its legal implications")
    citation: str = Field(description="Legal precedent, law code (e.g. Indian Contract Act), or web search validation findings")
    suggestion: str = Field(description="How the user should negotiate or rewrite this clause")

class ContractAuditReport(BaseModel):
    overall_score: int = Field(description="Safety score from 0 (very risky) to 100 (perfectly safe)")
    summary: str = Field(description="A brief 2-3 sentence overall summary of the contract's risk profile")
    risks: List[RiskItem] = Field(default=[], description="List of identified risk items")


class DocumentClassification(BaseModel):
    document_type: str = Field(description="One of CONTRACT, GOVERNMENT_FORM, POLICY_DOCUMENT, FINANCIAL_DOC, INFORMATIONAL, OTHER")
    confidence: float = Field(description="Classifier confidence between 0 and 1")
    reason: str = Field(description="Brief justification for the classification")
    proceed_with_analysis: bool = Field(description="True only if document_type is CONTRACT")


# 2. Tavily Search Client Integration
class TavilySearchClient:
    @staticmethod
    async def search(query: str, max_results: int = 3) -> str:
        """
        Executes a web search query on the Tavily API and returns formatted snippets.
        """
        if not settings.TAVILY_API_KEY:
            logger.warning("TAVILY_API_KEY is not configured. Web search tool is disabled.")
            return "No Tavily Search API key configured. Standard general precedents applied."
        
        try:
            url = "https://api.tavily.com/search"
            headers = {
                "Content-Type": "application/json"
            }
            payload = {
                "api_key": settings.TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "max_results": max_results
            }
            
            logger.info(f"Triggering Tavily Search API for query: '{query}'")
            print(f"\n[TAVILY SEARCH] Querying: '{query}'...")
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload, timeout=12.0)
                
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    print(f"    - Found {len(results)} web search results.")
                    
                    if not results:
                        return f"No web search results found for query: '{query}'"
                        
                    snippets = []
                    for idx, res in enumerate(results):
                        title = res.get("title", "No Title")
                        url_link = res.get("url", "")
                        desc = res.get("content", "No description available.")
                        # Cap snippet length so N search results don't blow up the
                        # Stage 3 synthesis prompt token count.
                        if len(desc) > TAVILY_SNIPPET_MAX_CHARS:
                            desc = desc[:TAVILY_SNIPPET_MAX_CHARS] + "..."
                        snippets.append(f"[{idx+1}] Source: {url_link}\nTitle: {title}\nSnippet: {desc}")
                    
                    return "\n\n".join(snippets)
                else:
                    logger.error(f"Tavily Search API returned error code {response.status_code}")
                    print(f"    [ERROR] Tavily Search API error: HTTP {response.status_code}")
                    return f"Tavily Search API error: HTTP {response.status_code}"
                    
        except Exception as e:
            logger.error(f"Tavily Search HTTP request failed: {e}")
            print(f"    [ERROR] Tavily Search failed: {str(e)}")
            return f"Tavily Search failed: {str(e)}"



CLASSIFIER_SYSTEM_PROMPT = """You are a document type classifier. Your ONLY job is to 
determine whether the uploaded document is a legal contract 
or agreement that requires risk analysis.

Read the first 500 words of the document and classify it 
into EXACTLY one of these categories:

CONTRACT       → Any agreement, NDA, MOU, service agreement, 
                 employment contract, lease deed, terms of 
                 service, or any document where two or more 
                 parties agree to binding legal obligations.

GOVERNMENT_FORM → Police forms, tax filings, registration 
                  certificates, government applications, 
                  licenses, permits.

POLICY_DOCUMENT → Company policies, HR handbooks, privacy 
                  policies, compliance guidelines.

FINANCIAL_DOC   → Invoices, receipts, financial statements, 
                  bank statements, audit reports.

INFORMATIONAL   → Research papers, news articles, brochures, 
                  manuals, academic documents.

OTHER           → Anything that does not fit above.

STRICT RULES:
1. Return ONLY a JSON object, nothing else.
2. Do NOT perform any risk analysis in this step.
3. If you are even 30% unsure it is a CONTRACT, classify 
   it as the more specific category.

Return exactly this format:
{
  "document_type": "CONTRACT",
  "confidence": 0.95,
  "reason": "Document contains parties, obligations, and 
             signature blocks typical of a legal agreement",
  "proceed_with_analysis": true
}

If document_type is NOT CONTRACT, set:
  "proceed_with_analysis": false"""


async def classify_document(text: str) -> dict:
    """
    Classify document type before risk analysis based on the first ~600 words of text.
    """
    # Only send first 600 words to save tokens
    preview = " ".join(text.split()[:600])
    
    prompt = f"""{CLASSIFIER_SYSTEM_PROMPT}

Classify this document:

{preview}
"""
    chat_structured = get_structured_llm_client(DocumentClassification, temperature=0.1)
    try:
        result = await chat_structured.ainvoke([HumanMessage(content=prompt)])
        return result.model_dump()
    except Exception as e:
        logger.error(f"Error during document classification: {e}")
        # If JSON parse fails, default to safe — don't analyze
        return {
            "document_type": "UNKNOWN",
            "confidence": 0.0,
            "proceed_with_analysis": False,
            "reason": f"Could not classify document type: {str(e)}"
        }


# 4. Agentic Auditing Loop
async def run_contract_audit(document_id: str) -> Dict[str, Any]:
    """
    Two-stage agentic contract audit runner:
    Stage 1: Scan retrieved contract context and generate Tavily search queries for suspicious clauses.
    Stage 2: Run searches in parallel, merge snippets, synthesize final compliance report, and compute score.
    """
    logger.info(f"Initiating Agentic Legal Audit for document {document_id}...")
    
    # Fetch Document Metadata
    documents_collection = Database.get_documents_collection()
    doc = await documents_collection.find_one({"_id": ObjectId(document_id)})
    if not doc:
        raise ValueError(f"Document with ID {document_id} not found in database.")
    
    filename = doc.get("filename", "Unknown Document")
    print(f"\n[LEGAL EAGLE] Starting compliance risk audit for contract: '{filename}'...")

    # Fetch first few chunks in order to construct a ~600-word preview text
    chunks_collection = Database.get_chunks_collection()
    cursor = chunks_collection.find({"document_id": ObjectId(document_id)}).sort([("metadata.page_number", 1), ("metadata.chunk_index", 1)])
    preview_texts = []
    word_count = 0
    async for chunk in cursor:
        chunk_text = chunk.get("text", "")
        preview_texts.append(chunk_text)
        word_count += len(chunk_text.split())
        if word_count >= 600:
            break
            
    document_preview = " ".join(preview_texts)
    
    # Classify the document type
    classification = await classify_document(document_preview)
    logger.info(f"Document {document_id} classification result: {classification}")
    
    if not classification.get("proceed_with_analysis", False):
        doc_type = classification.get("document_type", "UNKNOWN")
        print(f"    [WARN] [BREACH] Document is not a legal contract (type: {doc_type}). Skipping compliance audit.")
        return {
            "is_contract": False,
            "document_type": doc_type,
            "confidence": classification.get("confidence", 0.0),
            "reason": classification.get("reason", "Not classified as a contract"),
            "overall_score": 100,
            "summary": f"This document appears to be a {doc_type}, not a legal contract. "
                       f"BREACH only analyzes binding legal agreements between parties.",
            "risks": []
        }
    
    # Define checklist for systematic risk discovery based on the 10 core risks:
    # liability, indemnification, confidentiality, non-compete, non-solicitation, 
    # ip_assignment, termination, governing_law, arbitration, renewal.
    LEGAL_CHECKLIST = {
        "liability": {
            "db_categories": ["Liability", "liability"],
            "search_term": "standard software contract limitation of liability cap India"
        },
        "indemnification": {
            "db_categories": ["Indemnification", "indemnification", "Indemnity", "indemnity"],
            "search_term": "standard indemnity clause enforceability India"
        },
        "confidentiality": {
            "db_categories": ["Confidentiality", "confidentiality"],
            "search_term": "perpetual NDA duration enforceability India"
        },
        "non-compete": {
            "db_categories": ["Non-Compete", "non-compete", "Non-compete"],
            "search_term": "Section 27 Indian Contract Act non-compete clause validity"
        },
        "non-solicitation": {
            "db_categories": ["Non-Solicitation", "non-solicitation", "Non-solicitation"],
            "search_term": "non-solicitation clause enforceability validity India"
        },
        "ip_assignment": {
            "db_categories": ["IP Assignment", "ip_assignment", "IP assignment", "Intellectual Property", "intellectual_property"],
            "search_term": "IP assignment clause standard Indian software contract"
        },
        "termination": {
            "db_categories": ["Termination", "termination"],
            "search_term": "notice period for termination without cause standard contracts"
        },
        "governing_law": {
            "db_categories": ["Governing Law", "governing_law", "Governing law", "Jurisdiction", "jurisdiction"],
            "search_term": "choice of governing law validity Indian contracts"
        },
        "arbitration": {
            "db_categories": ["Arbitration", "arbitration", "Dispute Resolution", "dispute_resolution", "Dispute resolution"],
            "search_term": "arbitration clause validity seat venue Indian arbitration act"
        },
        "renewal": {
            "db_categories": ["Renewal", "renewal"],
            "search_term": "contract auto-renewal clause standard notice period"
        }
    }

    # 1. Determine which checklist categories are actually present in this document.
    # Clause chunks were already category-tagged during structured extraction
    # (extract_and_chunk_pdf_document), so this is a single DB lookup with zero
    # LLM calls, instead of the previous 10 separate per-category LLM checks
    # (whose risk output was never even fed into the Stage 3 synthesis prompt below).
    logger.info("Determining present risk categories via DB lookup (no LLM calls)...")
    print("[LEGAL EAGLE] Scanning contract checklist via categorized chunks (no LLM calls)...")

    present_categories = set(await chunks_collection.distinct(
        "category", {"document_id": ObjectId(document_id)}
    ))

    search_queries = []
    for info in LEGAL_CHECKLIST.values():
        if present_categories.intersection(info["db_categories"]):
            search_queries.append(info["search_term"])

    logger.info(f"Checklist scan complete. {len(search_queries)} risk categories present with matching clauses.")
    print(f"[LEGAL EAGLE] Scan complete. {len(search_queries)} risk categories present for web search lookup.")

    # 2. Build unified context_str for final synthesis
    all_db_categories = []
    for info in LEGAL_CHECKLIST.values():
        all_db_categories.extend(info["db_categories"])
        
    cursor = chunks_collection.find({
        "document_id": ObjectId(document_id),
        "category": {"$in": all_db_categories}
    })
    
    context_clauses = []
    seen_c_keys = set()
    async for chunk in cursor:
        chunk_key = f"{chunk['metadata']['page_number']}_{chunk['text'][:100]}"
        if chunk_key not in seen_c_keys:
            seen_c_keys.add(chunk_key)
            context_clauses.append(chunk)
            if len(context_clauses) >= MAX_CONTEXT_CLAUSES:
                break

    if not context_clauses:
        # Fallback to all chunks
        cursor = chunks_collection.find({"document_id": ObjectId(document_id)})
        async for chunk in cursor:
            chunk_key = f"{chunk['metadata']['page_number']}_{chunk['text'][:100]}"
            if chunk_key not in seen_c_keys:
                seen_c_keys.add(chunk_key)
                context_clauses.append(chunk)
                if len(context_clauses) >= MAX_CONTEXT_CLAUSES:
                    break

    def _build_context_str(clauses: List[Dict[str, Any]], max_clause_chars: int = MAX_CLAUSE_CHARS) -> str:
        parts = []
        for idx, c in enumerate(clauses):
            page = c.get("metadata", {}).get("page_number", "Unknown") if "metadata" in c else c.get("page_number", "Unknown")
            ref = c.get("clause_reference", "N/A")
            cat = c.get("category", "General")
            text = c["text"]
            if len(text) > max_clause_chars:
                text = text[:max_clause_chars] + "..."
            parts.append(f"--- Clause Option {idx + 1} (Page {page}) [Ref: {ref}, Category: {cat}] ---\n{text}\n\n")
        return "".join(parts)

    context_str = _build_context_str(context_clauses)
        
    # --- STAGE 2: Execute Web Search in Parallel ---
    web_context_str = ""
    if search_queries and settings.TAVILY_API_KEY:
        logger.info("Executing Stage 2: Performing parallel Tavily Web Searches...")
        print(f"[LEGAL EAGLE] Stage 2: Running parallel Tavily Web Search queries for {min(len(search_queries), MAX_SEARCH_QUERIES)} categories...")
        tavily_semaphore = asyncio.Semaphore(4)

        async def bounded_search(query: str):
            async with tavily_semaphore:
                return await TavilySearchClient.search(query)

        queries_executed = search_queries[:MAX_SEARCH_QUERIES]  # Limit categories to protect API rate limits and Stage 3 prompt size
        search_results = await asyncio.gather(*[bounded_search(q) for q in queries_executed])

        for idx, res_text in enumerate(search_results):
            web_context_str += f"=== Web Search for: '{queries_executed[idx]}' ===\n{res_text}\n\n"
    else:
        logger.info("Skipping Tavily Web Search (no key configured or no risk categories present).")
        web_context_str = "Web search is disabled. Analyzing based on default legal knowledge guidelines."
        
    # --- STAGE 3: Final Synthesis & Scoring ---
    logger.info("Executing Stage 3: LLM Synthesis and Compliance Audit Compilation...")

    def _build_stage3_prompt(context_block: str, web_block: str) -> str:
        return f"""IMPORTANT: This document may contain many risky clauses.
You MUST scan ALL clauses before stopping.
Do not stop after finding 5 risks.
Minimum 8 risks must be evaluated for any HIGH RISK document.
The more HIGH severity risks found, the lower the score.

You are a senior corporate compliance lawyer. 
Review the contract file '{filename}' and compile the final legal risk audit report.
You must combine:
1. The raw contract text clauses.
2. The Tavily Web Search results (precedents, enforceability rules, and standard practices under Indian law).

CRITICAL GROUNDING RULES — READ BEFORE EVERY ANALYSIS:

RULE 1 — QUOTE BEFORE YOU FLAG:
Before flagging ANY risk, you MUST find the exact sentence 
in the document that supports it. If you cannot quote it 
verbatim, DO NOT flag it. No exceptions.

RULE 2 — NO INFERENCE OF MISSING CLAUSES:
If a clause does not exist in the document, do not flag 
its absence as a risk unless specifically asked.
"This contract lacks X" is only valid if X is legally 
required. Do not invent missing clauses.

RULE 3 — VERIFY CLAUSE NUMBERS:
When citing a clause, verify the clause number matches 
the quoted text. Do not guess clause locations.

RULE 4 — NO CROSS-DOCUMENT CONTAMINATION:
Do not apply knowledge of previous documents analysed 
in this session to the current document. Each document 
is independent.

SELF-CHECK before each flagged risk, ask yourself:
  Q1: Can I paste the exact sentence from the document?
  Q2: Is this sentence actually in THIS document?
  Q3: Would a human lawyer agree this is risky?
  
If any answer is NO → do not flag it.

If the document is not a legal contract (e.g. a government form, invoice, policy, receipt), respond with exactly:
{{
  "overall_score": 100,
  "summary": "This does not appear to be a legal contract. Risk analysis is not applicable.",
  "risks": []
}}

CRITICAL INSTRUCTIONS FOR RISK SCREENING:
1. EXHAUSTIVE SCREENING REQUIRED: Analyze every clause. Do not omit any risk. List ALL identified legal warning signs.
2. CHECK FOR LIABILITY CAPS: Carefully look for liability asymmetry (e.g. one party having unlimited liability or capped at contract value, while the other party limits their liability to a nominal amount like ₹500 or $100). Flag this as HIGH risk.
3. CHECK FOR INDEMNIFICATION: Flag one-sided indemnities, obligations to defend and hold harmless, or extreme indemnity triggers as HIGH or MEDIUM risk.
4. CHECK FOR PERPETUAL CONFIDENTIALITY: Flag infinite or perpetual NDA durations (e.g. confidentiality obligations remaining in force forever or surviving indefinitely) as MEDIUM or HIGH risk.
5. CHECK FOR NON-COMPETE: Flag covenants in restraint of trade, post-employment non-competes, and geographic restrictions which are void under Section 27 of the Indian Contract Act as HIGH risk.
6. CHECK FOR NON-SOLICITATION: Flag aggressive employee, customer, or client non-solicit covenants as MEDIUM risk.
7. CHECK FOR IP ASSIGNMENT: Flag unfavorable intellectual property assignments, broad copyright/patent assignments, or forfeiture of background intellectual property as HIGH or MEDIUM risk.
8. CHECK FOR TERMINATION: Flag unilateral termination without cause, auto-renewal notification traps (e.g. 90-day notify window), or excessive notice periods as HIGH or MEDIUM risk.
9. CHECK FOR GOVERNING LAW: Flag unfavorable choice of governing law, non-local jurisdictions, or waivers of local judicial resort as MEDIUM risk.
10. CHECK FOR ARBITRATION: Flag one-sided arbitration clauses, inconvenient seats/venues, split arbitration costs, and waiver of class actions or right to sue in court as MEDIUM risk.
11. CHECK FOR RENEWAL: Flag auto-renewal traps, unilateral price escalation terms upon renewal, and notice duration limits as MEDIUM risk.

REPORT QUALITY REQUIREMENTS — EVERY RISK MUST INCLUDE ALL 5 ELEMENTS:

1. WHAT IT SAYS — quote the clause verbatim in the clause_text field (already required ✅)

2. WHAT IT MEANS IN PLAIN ENGLISH — in the explanation field, start with "This means..." and explain
   in non-lawyer language what this clause actually does to the signing party in practice.
   What power does it give the other party? What does the signing party lose or risk?

3. WHY THIS SEVERITY — still in the explanation field, after the plain-English explanation,
   justify exactly why you chose HIGH, MEDIUM, or LOW:
   - HIGH   → "This is rated HIGH because this clause could result in direct financial harm,
               personal liability of directors, or permanent loss of rights if enforced."
   - MEDIUM → "This is rated MEDIUM because this clause creates imbalance but has limited
               immediate financial exposure and is common in negotiation."
   - LOW    → "This is rated LOW because it is a minor concern and a standard negotiation point."
   Then state the worst-case scenario if the clause is enforced as written.

4. INDIAN LAW CONTEXT — in the citation field, cite the SPECIFIC section of the relevant Indian
   law (Indian Contract Act 1872, IT Act, Companies Act, etc.) and explain how Indian courts
   have historically treated this type of clause. Reference the web search results where applicable.

5. SPECIFIC REMEDIATION — in the suggestion field, give a clause-specific rewrite or negotiation
   position. Do NOT give generic advice like "cap liability at contract value".
   Name the specific clause number, propose exact replacement language or a concrete ask,
   and explain what the party should insist on in negotiation.

SCORING INSTRUCTIONS — FOLLOW EXACTLY, NO EXCEPTIONS:

After identifying all risks, calculate the score like this:

  Step 1: Start at 100
  Step 2: For each HIGH severity risk:    subtract 15
  Step 3: For each MEDIUM severity risk:  subtract 8
  Step 4: For each LOW severity risk:     subtract 3
  Step 5: Floor at 0 (score cannot go below 0)

THEN assign risk label:
  85-100  → LOW RISK - SAFE
  60-84   → MEDIUM RISK - WARNING
  30-59   → HIGH RISK - DANGER
  0-29    → CRITICAL RISK - DO NOT SIGN

DO NOT round up. DO NOT use judgment to override the formula.
The formula IS the score.

EXECUTIVE SUMMARY REQUIREMENTS — the summary field must be 3-4 sentences and include ALL of:
- A one-line description of what the contract is and who it is between
- The total count of HIGH, MEDIUM, and LOW risks found
- A clear verdict on one of exactly three lines: "VERDICT: SIGN" / "VERDICT: NEGOTIATE" / "VERDICT: DO NOT SIGN"
- The 2-3 most dangerous specific clause references by name/number
- A one-sentence estimate of litigation risk if the contract is signed as-is
Do NOT use bullet points, markdown, or score arithmetic in the summary. Write it as flowing prose.

You must respond ONLY with a valid JSON document matching the following keys:
{{
  "overall_score": 25,
  "summary": "OmniCore's Predatory Agreement (2024) is a heavily one-sided services contract between OmniCore Technologies and the Partner. Of the clauses reviewed, 5 are rated HIGH severity, 2 MEDIUM, and 1 LOW — exposing the Partner to unlimited personal financial liability, permanent IP forfeiture, and a 10-year global non-compete void under Indian law. VERDICT: DO NOT SIGN. Clauses 2.1 (unlimited liability), 4.1 (IP assignment), and 5.3 (non-compete) must be renegotiated before execution. If enforced as written, the contract carries significant litigation risk and would likely result in protracted disputes in courts unfavorable to the Partner.",
  "risks": [
    {{
      "category": "Liability",
      "clause_text": "The Partner shall bear unlimited liability for any breach of this Agreement, including consequential and indirect damages.",
      "severity": "HIGH",
      "severity_color": "red",
      "explanation": "This means that if the Partner breaches any part of this contract — even accidentally — the Company can sue the Partner for any amount of money with no ceiling, including the personal assets of the Partner's directors. This is rated HIGH because it could result in direct, unlimited financial harm and personal liability of company founders if enforced. In the worst case, a single data leak or missed deadline could result in a lawsuit that bankrupts the Partner entity and its founders personally.",
      "citation": "Under Section 124 of the Indian Contract Act 1872, indemnity clauses are valid and enforceable. However, Indian courts in Kisan Sahkari Chini Mills Ltd v. Vardarajan (2005) have held that clauses imposing unconscionable or unlimited burdens on one party may be set aside under Section 23 as void against public policy. This clause would face challenge but carries litigation risk until overturned.",
      "suggestion": "Negotiate a mutual, capped liability clause: both parties' liability should be limited to the total fees paid or payable in the 12 months preceding the breach. Specifically for Clause 2.1, propose: 'Each party's aggregate liability shall not exceed INR [X] or the total fees paid in the preceding 12 months, whichever is higher. Neither party shall be liable for indirect, consequential, or punitive damages.'"
    }}
  ]
}}

Contract Clauses:
{context_block}

Web Search Precedents & Legal Context:
{web_block}

Respond strictly in the JSON format above, with no markdown formatting, no introductory text, and no concluding words. Must be a clean JSON document."""

    print("[LEGAL EAGLE] Stage 3: Synthesizing search results and compiling final audit report...")
    chat_structured = get_structured_llm_client(ContractAuditReport, temperature=0.1)
    stage2_prompt = _build_stage3_prompt(context_str, web_context_str)

    try:
        try:
            result_stage2 = await chat_structured.ainvoke([HumanMessage(content=stage2_prompt)])
        except Exception as synth_err:
            if any(marker in str(synth_err).lower() for marker in TOKEN_LIMIT_ERROR_MARKERS):
                # Provider rejected the request for exceeding its tokens-per-minute quota.
                # Retry once with a much smaller prompt (fewer/shorter clauses, no web
                # search context) rather than dropping the audit entirely.
                logger.warning(
                    f"Stage 3 synthesis hit a provider token limit ({synth_err}); retrying with a reduced prompt."
                )
                print("[LEGAL EAGLE] Stage 3 prompt exceeded provider token limits. Retrying with reduced context...")
                reduced_context_str = _build_context_str(context_clauses[:10], max_clause_chars=400)
                reduced_prompt = _build_stage3_prompt(
                    reduced_context_str,
                    "Web search context omitted to stay within provider token limits."
                )
                result_stage2 = await chat_structured.ainvoke([HumanMessage(content=reduced_prompt)])
            else:
                raise

        # result_stage2 is already validated against ContractAuditReport by
        # with_structured_output - a malformed/incomplete LLM response would
        # have raised above rather than reaching this line.
        final_audit_data = result_stage2.model_dump()

        # Validate and sanitize overall score
        score = final_audit_data.get("overall_score", 100)
        try:
            score = int(score)
            score = max(0, min(100, score))
        except Exception:
            score = 75
            
        final_audit_data["overall_score"] = score
        final_audit_data["is_contract"] = True
        final_audit_data["document_type"] = "CONTRACT"
        
        # Verify risks list exists
        if "risks" not in final_audit_data:
            final_audit_data["risks"] = []
            
        logger.info(f"Legal Audit compiled successfully. Safety Score: {score}/100. Risks identified: {len(final_audit_data['risks'])}")
        print(f"[LEGAL EAGLE] Stage 3 complete. Overall Safety Score: {score}/100. Flagged risks: {len(final_audit_data['risks'])}")
        return final_audit_data
        
    except Exception as parse_err2:
        logger.error(f"Failed to obtain a valid Stage 3 audit report: {parse_err2}")
        # Return fallback audit structure
        return {
            "overall_score": 50,
            "summary": "Failed to parse final compliance audit from LLM. Please re-run the analysis.",
            "risks": [
                {
                    "category": "Other",
                    "clause_text": "Audit execution encountered a parsing error.",
                    "severity": "MEDIUM",
                    "severity_color": "yellow",
                    "explanation": f"The LLM response could not be parsed as valid JSON: {str(parse_err2)}",
                    "citation": "N/A",
                    "suggestion": "Please retry the audit or upload the document again."
                }
            ]
        }
