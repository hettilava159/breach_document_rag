import asyncio
import os
import sys
from bson import ObjectId
from datetime import datetime
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

from app.database import init_db, Database
from app.services.agent_service import run_contract_audit

async def test_audit():
    await init_db()
    documents_collection = Database.get_documents_collection()
    chunks_collection = Database.get_chunks_collection()
    
    # ----------------------------------------------------
    # TEST case 1: Mock Contract NDA (Should Proceed)
    # ----------------------------------------------------
    doc_id_1 = ObjectId()
    doc_data_1 = {
        "_id": doc_id_1,
        "filename": "mock_risky_NDA.pdf",
        "file_size": 2048,
        "status": "processed",
        "uploaded_at": datetime.utcnow(),
        "title": "Mock NDA Agreement",
        "author": "Acme Corp",
        "processing_progress": 100,
        "has_context": False,
        "has_audit": False
    }
    
    # NDA Chunks
    mock_chunks_1 = [
        {
            "document_id": doc_id_1,
            "text": "MUTUAL NON-DISCLOSURE AND CONFIDENTIALITY AGREEMENT\nThis NDA is entered into between Acme Corp and Partner Inc to govern the sharing of proprietary technology.",
            "clause_reference": "Intro",
            "category": "General",
            "metadata": {"page_number": 1, "chunk_index": 0}
        },
        {
            "document_id": doc_id_1,
            "text": "Clause 2.2: Confidentiality Duration. The obligations of confidentiality under this Agreement shall survive the termination of this Agreement and remain in force in perpetuity forever.",
            "clause_reference": "2.2",
            "category": "Confidentiality",
            "metadata": {"page_number": 1, "chunk_index": 1}
        },
        {
            "document_id": doc_id_1,
            "text": "Clause 3.3: Limitation of Liability. The Recipient shall have unlimited liability for any breaches of this Agreement. Under no circumstances shall Acme Corp's liability exceed Five Hundred Indian Rupees (INR 500) for any claims.",
            "clause_reference": "3.3",
            "category": "Liability",
            "metadata": {"page_number": 1, "chunk_index": 2}
        }
    ]
    
    await documents_collection.insert_one(doc_data_1)
    await chunks_collection.insert_many(mock_chunks_1)
    
    print("\n>>> Running Audit on Mock Contract NDA...")
    try:
        report_1 = await run_contract_audit(str(doc_id_1))
        print("NDA Result:")
        print(f"  Is Contract: {report_1.get('is_contract')}")
        print(f"  Document Type: {report_1.get('document_type')}")
        print(f"  Overall Score: {report_1.get('overall_score')}")
        print(f"  Flagged Risks Count: {len(report_1.get('risks', []))}")
    except Exception as e:
        print(f"NDA test failed: {e}")
    finally:
        await documents_collection.delete_one({"_id": doc_id_1})
        await chunks_collection.delete_many({"document_id": doc_id_1})

    # ----------------------------------------------------
    # TEST Case 2: Mock Mumbai Police Form (Should Reject)
    # ----------------------------------------------------
    doc_id_2 = ObjectId()
    doc_data_2 = {
        "_id": doc_id_2,
        "filename": "mumbai_police_tenant.pdf",
        "file_size": 1024,
        "status": "processed",
        "uploaded_at": datetime.utcnow(),
        "title": "Tenant Registration Form",
        "author": "Government of Maharashtra",
        "processing_progress": 100,
        "has_context": False,
        "has_audit": False
    }
    
    # Government form chunks
    mock_chunks_2 = [
        {
            "document_id": doc_id_2,
            "text": "GREATER MUMBAI POLICE DEPARTMENT\nINFORMATION ABOUT TENANT/REPRESENTATIVE REGISTRATION FORM\nThis is an informational document to be filled by the landlord to report details of the tenant to the local police station.",
            "clause_reference": "N/A",
            "category": "General",
            "metadata": {"page_number": 1, "chunk_index": 0}
        },
        {
            "document_id": doc_id_2,
            "text": "Section A: Property Owner Details. Name: Pranav Mandani, Address: Flat 604, Crystal Tower, Mumbai, Maharashtra.\nSection B: Tenant Details. Name: John Doe, Permanent Address: Pune.",
            "clause_reference": "N/A",
            "category": "General",
            "metadata": {"page_number": 1, "chunk_index": 1}
        },
        {
            "document_id": doc_id_2,
            "text": "Disclaimer: I hereby declare that the information provided above is true to the best of my knowledge. Signature of Landlord/Owner.",
            "clause_reference": "N/A",
            "category": "General",
            "metadata": {"page_number": 1, "chunk_index": 2}
        }
    ]
    
    await documents_collection.insert_one(doc_data_2)
    await chunks_collection.insert_many(mock_chunks_2)
    
    print("\n>>> Running Audit on Mock Government Form...")
    try:
        report_2 = await run_contract_audit(str(doc_id_2))
        print("Government Form Result:")
        print(f"  Is Contract: {report_2.get('is_contract')}")
        print(f"  Document Type: {report_2.get('document_type')}")
        print(f"  Confidence: {report_2.get('confidence')}")
        print(f"  Reason: {report_2.get('reason')}")
        print(f"  Overall Score: {report_2.get('overall_score')}")
        print(f"  Flagged Risks Count: {len(report_2.get('risks', []))}")
    except Exception as e:
        print(f"Government Form test failed: {e}")
    finally:
        await documents_collection.delete_one({"_id": doc_id_2})
        await chunks_collection.delete_many({"document_id": doc_id_2})
        
    await Database.disconnect()

if __name__ == "__main__":
    asyncio.run(test_audit())
