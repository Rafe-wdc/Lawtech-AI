from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
import fitz
import os
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from utils.rewrite_query import rewrite_query_with_context
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from typing import List, Optional
from werkzeug.utils import secure_filename
import tempfile
import time
from utils.text_extraction import extract_text_with_mask_from_pdf_with_gemini
from utils.pdf_utils import compress_pdf
from utils.guardrails import run_input_guardrails, run_output_guardrails
from utils.pdf_chat_history import load_pdf_chat_history, save_pdf_chat_history
from utils.embeddings import qa_embeddings as qa_instructor_embeddings

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

import asyncio
import base64
import io
import shutil

router = APIRouter(prefix="/pyapi", tags=["mainqa"])

# Constants
MAX_BATCH_SIZE = 20
MAX_PARALLEL_REQUESTS = 10
DPI = 150
TEXT_PER_PAGE_THRESHOLD = 700
MAX_SIZE = 314572800  # 300MB
MAX_PAGES = 500

# Temporary storage directory for uploaded files
TEMP_UPLOAD_DIR = './temp_uploads'
os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)

CHROMA_STORE_ROOT = os.path.realpath('./chroma_store')

def _safe_persist_dir(unique_string: str) -> str:
    """Sanitize unique_string to prevent path traversal."""
    safe_name = secure_filename(unique_string)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid uniqueString")
    persist_dir = os.path.realpath(os.path.join(CHROMA_STORE_ROOT, safe_name))
    if not persist_dir.startswith(CHROMA_STORE_ROOT):
        raise HTTPException(status_code=400, detail="Invalid uniqueString")
    return persist_dir

def _safe_temp_dir(unique_string: str) -> str:
    """Sanitize unique_string for temp upload path."""
    safe_name = secure_filename(unique_string)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid uniqueString")
    temp_root = os.path.realpath(TEMP_UPLOAD_DIR)
    temp_dir = os.path.realpath(os.path.join(temp_root, safe_name))
    if not temp_dir.startswith(temp_root):
        raise HTTPException(status_code=400, detail="Invalid uniqueString")
    return temp_dir


# ============================================================================
# HELPER FUNCTIONS (kept same as original)
# ============================================================================

async def extract_pdf_intelligently(pdf_path, google_api_key):
    doc = fitz.open(pdf_path)
    num_pages = len(doc)

    extracted_text = []
    batches = []
    batch_images = []
    text_length = 0
    has_images = False

    for i, page in enumerate(doc):
        page_text = page.get_text().strip()
        extracted_text.append(page_text)
        text_length += len(page_text)

        if page.get_images(full=True):
            has_images = True

        pix = page.get_pixmap(dpi=DPI)
        b64_img = base64.b64encode(pix.tobytes("png")).decode("utf-8")
        batch_images.append(b64_img)

        if len(batch_images) == MAX_BATCH_SIZE:
            start_page = i - len(batch_images) + 1
            batches.append((batch_images.copy(), start_page))
            batch_images = []

    if batch_images:
        start_page = num_pages - len(batch_images)
        batches.append((batch_images.copy(), start_page))

    print(f"PDF has {num_pages} pages")
    print(f"Extracted raw text length = {text_length}")
    print(f"Batches prepared = {len(batches)}")
    print(f"Images detected? {has_images}")

    # if text_length > num_pages * TEXT_PER_PAGE_THRESHOLD and not has_images:
    if text_length > num_pages * TEXT_PER_PAGE_THRESHOLD:
        print("✔ Using normal text extraction")
        return "\n".join(extracted_text)

    print("⚠ Using Vision fallback")
    return await run_parallel_vision(batches, google_api_key)


async def run_parallel_vision(batches, google_api_key):
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-lite",
        google_api_key=google_api_key,
        temperature=0,
        max_output_tokens=2048
    )

    semaphore = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)

    async def process_batch(batch_b64, batch_start_page):
        content = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Extract all visible text and markdown tables from these document images "
                            f"(Pages {batch_start_page + 1} to {batch_start_page + len(batch_b64)}). "
                            "If anything is unclear, infer probable text."
                        ),
                    },
                    *[
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}
                        }
                        for b64 in batch_b64
                    ],
                ],
            }
        ]

        loop = asyncio.get_running_loop()
        async with semaphore:
            resp = await loop.run_in_executor(None, llm.invoke, content)
            return f"--- Pages {batch_start_page + 1}-{batch_start_page + len(batch_b64)} ---\n{resp.content.strip()}"

    tasks = [
        asyncio.create_task(process_batch(b64s, start))
        for b64s, start in batches
    ]

    results = await asyncio.gather(*tasks)
    return "\n\n".join(results)


def get_temp_upload_path(unique_string):
    """Get the temporary upload directory for a unique string (sanitized)."""
    return _safe_temp_dir(unique_string)


# ============================================================================
# API ENDPOINT 1: UPLOAD & VALIDATE (stores files temporarily)
# ============================================================================

@router.post("/upload_validate")
async def upload_validate(
    file: List[UploadFile] = File(...),
    uniqueString: str = Form(...)
):
    """
    Validates uploaded PDF files for size and page count limits.
    Stores valid files temporarily for later processing.
    """
    try:
        # Validation
        if not uniqueString:
            raise HTTPException(
                status_code=400,
                detail={'code': 'MISSING_UNIQUE_STRING', 'message': 'uniqueString is required'}
            )
        
        if not file or all(f.filename == '' for f in file):
            raise HTTPException(
                status_code=400,
                detail={'code': 'NO_FILES', 'message': 'No files selected'}
            )

        # Create temporary directory for this upload session
        temp_dir = get_temp_upload_path(uniqueString)
        os.makedirs(temp_dir, exist_ok=True)

        validation_results = []
        all_valid = True
        stored_files = []

        for filex in file:
            if filex and filex.filename.lower().endswith('.pdf'):
                filename = secure_filename(filex.filename)
                
                try:
                    # Create temporary file for validation
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                        filex.file.seek(0)
                        content = await filex.read()
                        tmp_pdf.write(content)
                        tmp_pdf_path = tmp_pdf.name

                    # Get file size
                    original_size = os.path.getsize(tmp_pdf_path)
                    
                    # Get page count
                    with fitz.open(tmp_pdf_path) as doc_check:
                        num_pages_check = len(doc_check)

                    # Check limits
                    size_valid = original_size <= MAX_SIZE
                    pages_valid = num_pages_check <= MAX_PAGES
                    
                    file_result = {
                        'filename': filename,
                        'size_bytes': original_size,
                        'size_mb': round(original_size / (1024 * 1024), 2),
                        'pages': num_pages_check,
                        'size_valid': size_valid,
                        'pages_valid': pages_valid,
                        'valid': size_valid and pages_valid
                    }

                    if not size_valid:
                        file_result['error'] = f'File exceeds 300MB limit'
                        all_valid = False
                        os.unlink(tmp_pdf_path)  # Delete invalid file
                    elif not pages_valid:
                        file_result['error'] = f'File exceeds 500 pages limit'
                        all_valid = False
                        os.unlink(tmp_pdf_path)  # Delete invalid file
                    else:
                        # Store valid file in temp directory
                        stored_path = os.path.join(temp_dir, filename)
                        shutil.move(tmp_pdf_path, stored_path)
                        stored_files.append(filename)
                        file_result['stored'] = True
                        print(f"✅ Stored: {filename} at {stored_path}")

                    validation_results.append(file_result)

                except Exception as e:
                    validation_results.append({
                        'filename': filename,
                        'valid': False,
                        'error': f'Validation error: {str(e)}'
                    })
                    all_valid = False
                    # Clean up temp file if it exists
                    if 'tmp_pdf_path' in locals() and os.path.exists(tmp_pdf_path):
                        os.unlink(tmp_pdf_path)
            else:
                validation_results.append({
                    'filename': filex.filename if filex else 'unknown',
                    'valid': False,
                    'error': 'Not a PDF file'
                })
                all_valid = False

        # If no valid files, clean up temp directory
        if not stored_files:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

        return {
            'status': all_valid,
            'uniqueString': uniqueString,
            'files': validation_results,
            'total_files': len(validation_results),
            'valid_files': len(stored_files),
            'stored_files': stored_files,
            'message': f'{len(stored_files)} file(s) validated and stored. Call /processing to process them.'
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Validation error: {e}")
        raise HTTPException(status_code=500, detail=f"Validation failed: {str(e)}")


# ============================================================================
# API ENDPOINT 2: PROCESSING (processes stored files)
# ============================================================================

class ProcessingRequest(BaseModel):
    uniqueString: str

@router.post("/processing")
async def processing(request: ProcessingRequest):
    """
    Processes previously uploaded and validated PDF files from temporary storage.
    Extracts text, creates chunks, and stores in Chroma DB.
    """
    try:
        start_time = time.time()
        unique_string = request.uniqueString

        # Validation
        if not unique_string:
            raise HTTPException(
                status_code=400,
                detail={'code': 'MISSING_UNIQUE_STRING', 'message': 'uniqueString is required'}
            )

        # Check if temp directory exists
        temp_dir = get_temp_upload_path(unique_string)
        if not os.path.exists(temp_dir):
            raise HTTPException(
                status_code=404,
                detail={
                    'code': 'NO_UPLOADED_FILES',
                    'message': f'No uploaded files found for uniqueString: {unique_string}. Please upload files first using /upload_validate'
                }
            )

        # Get list of stored PDF files (os.listdir returns files in arbitrary order)
        pdf_files = sorted([f for f in os.listdir(temp_dir) if f.lower().endswith('.pdf')])
        
        if not pdf_files:
            raise HTTPException(
                status_code=404,
                detail={
                    'code': 'NO_PDF_FILES',
                    'message': 'No PDF files found in temporary storage'
                }
            )

        print(f"Processing {len(pdf_files)} files from: {temp_dir}")

        documents = []
        processed_filenames = []

        for filename in pdf_files:
            try:
                file_path = os.path.join(temp_dir, filename)
                
                # Compress PDF
                compressed_path = compress_pdf(file_path)

                # Extract text intelligently
                doc = fitz.open(compressed_path)
                num_pages = len(doc)
                doc.close()
                
                print(f"Processing {filename} ({num_pages} pages)...")
                extracted_text = await extract_pdf_intelligently(compressed_path, GOOGLE_API_KEY)

                # Clean up compressed file if different from original
                if compressed_path != file_path and os.path.exists(compressed_path):
                    os.unlink(compressed_path)

                if extracted_text.strip():
                    doc = Document(page_content=extracted_text, metadata={'source': filename})
                    documents.append(doc)
                    processed_filenames.append(filename)
                    print(f"✅ Successfully processed: {filename}")
                else:
                    print(f"⚠️ No text extracted from {filename}")

            except Exception as e:
                print(f"❌ Error processing {filename}: {e}")
                # Continue processing other files instead of failing completely
                continue

        if not documents:
            # Clean up temp directory
            shutil.rmtree(temp_dir)
            raise HTTPException(
                status_code=422,
                detail={'code': 'NO_TEXT_EXTRACTED', 'message': 'No text could be extracted from any PDF'}
            )

        print(f"Documents processed: {len(documents)}")

        # Chunking
        split_start = time.time()
        text_splitter = RecursiveCharacterTextSplitter(
            separators=[""],
            chunk_size=15000,
            chunk_overlap=200
        )
        texts = text_splitter.split_documents(documents)
        print(f"Chunks created: {len(texts)} (Time: {round(time.time() - split_start, 2)}s)")

        # Chroma DB setup
        collection_name = f"collection_{unique_string}"
        persist_dir = _safe_persist_dir(unique_string)
        os.makedirs(persist_dir, exist_ok=True)

        print("Creating/Updating Chroma DB...")

        # Check if collection exists
        existing_filenames = []
        collection_exists = os.path.exists(os.path.join(persist_dir, 'chroma.sqlite3'))

        if collection_exists:
            print("Loading existing collection...")
            vectordb = Chroma(
                embedding_function=qa_instructor_embeddings,
                collection_name=collection_name,
                persist_directory=persist_dir
            )
            
            # Get existing metadata
            try:
                existing_metadata = vectordb._collection.metadata
                if existing_metadata:
                    existing_filenames_str = existing_metadata.get("filenames", "")
                    existing_filenames = existing_filenames_str.split(",") if existing_filenames_str else []
                    existing_filenames = [f for f in existing_filenames if f]
                    print(f"Existing filenames: {existing_filenames}")
            except Exception as e:
                print(f"Could not load existing metadata: {e}")
            
            # Add new documents
            vectordb.add_documents(texts)
            print("Added documents to existing collection")
        else:
            print("Creating new collection...")
            vectordb = Chroma.from_documents(
                documents=texts,
                embedding=qa_instructor_embeddings,
                collection_name=collection_name,
                persist_directory=persist_dir
            )

        # Combine filenames (preserve order - existing first, then new)
        # Remove duplicates while maintaining order
        seen = set()
        all_filenames = []
        for f in existing_filenames + processed_filenames:
            if f and f not in seen:
                seen.add(f)
                all_filenames.append(f)

        # Update metadata
        vectordb._collection.modify(
            metadata={
                "filenames": ",".join(all_filenames),
                "upload_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                "file_count": str(len(all_filenames))
            }
        )

        # Force persistence
        try:
            if hasattr(vectordb._client, 'persist'):
                vectordb._client.persist()
            if hasattr(vectordb._collection, '_client'):
                vectordb._collection._client.persist()
            time.sleep(0.2)
            
            # Verify
            check_metadata = vectordb._collection.metadata
            if check_metadata and check_metadata.get("filenames"):
                print(f"✅ Metadata persisted: {check_metadata.get('filenames')}")
        except Exception as e:
            print(f"Persist warning: {e}")

        # Clean up temporary directory after successful processing
        try:
            shutil.rmtree(temp_dir)
            print(f"✅ Cleaned up temp directory: {temp_dir}")
        except Exception as e:
            print(f"⚠️ Warning: Could not clean up temp directory: {e}")

        total_time = round(time.time() - start_time, 2)
        
        return {
            'status': True,
            'uniqueString': unique_string,
            'chunks': len(texts),
            'filenames': all_filenames,
            'new_files': processed_filenames,
            'total_files': len(all_filenames),
            'processing_time_sec': total_time,
            'message': 'Processing completed successfully. You can now use /chat to query the documents.'
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Processing error: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")


# ============================================================================
# API ENDPOINT 3: CHAT/QA
# ============================================================================

class ChatRequest(BaseModel):
    uniqueString: str
    question: str

@router.post("/chat")
async def chat(request: ChatRequest):
    """
    Answers questions based on the processed documents stored in Chroma DB.
    Supports multi-turn conversation with chat history stored locally
    in a JSON file keyed by uniqueString.
    """
    try:
        unique_string = request.uniqueString
        question = request.question

        # Validation
        if not unique_string:
            raise HTTPException(
                status_code=400,
                detail={'code': 'MISSING_UNIQUE_STRING', 'message': 'uniqueString is required'}
            )
        if not question:
            raise HTTPException(
                status_code=400,
                detail={'code': 'MISSING_QUESTION', 'message': 'question is required'}
            )

        # Input guardrails
        guardrail_result = run_input_guardrails(question)
        if guardrail_result.blocked:
            return {
                'status': False,
                'answer': guardrail_result.reason,
                'total_tokens_consumed': 0,
                'filenames': [],
                'upload_date': 'N/A',
                'file_count': '0',
                'uniqueString': unique_string
            }

        # Load chat history from local JSON file
        chat_history, all_chats = load_pdf_chat_history(unique_string)

        # Rewrite query with conversational context for better retrieval
        search_query = rewrite_query_with_context(question, chat_history)

        collection_name = f"collection_{unique_string}"
        persist_dir = _safe_persist_dir(unique_string)

        # Check if collection exists
        if not os.path.exists(persist_dir):
            raise HTTPException(
                status_code=404,
                detail={'code': 'COLLECTION_NOT_FOUND', 'message': f'No documents found for uniqueString: {unique_string}. Please upload and process documents first.'}
            )

        print(f"Loading Chroma DB from: {persist_dir}")

        vectordb = Chroma(
            embedding_function=qa_instructor_embeddings,
            collection_name=collection_name,
            persist_directory=persist_dir
        )

        # Retrieve metadata
        try:
            metadata = vectordb._collection.metadata
            filenames_str = metadata.get("filenames", "")
            filenames = filenames_str.split(",") if filenames_str else []
            upload_date = metadata.get("upload_date", "Unknown")
            file_count = metadata.get("file_count", "0")
            print(f"Loaded files: {filenames} (Total: {file_count})")
        except Exception as e:
            print(f"Could not retrieve metadata: {e}")
            filenames = []
            upload_date = "Unknown"
            file_count = "0"

        # Initialize LLM
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0.3)

        # Use MMR retriever for better diversity across files
        retriever = vectordb.as_retriever(
            search_type="mmr",
            search_kwargs={
                "k": 30,
                "fetch_k": 50,
                "lambda_mult": 0.5
            }
        )

        # Retrieve documents using the rewritten query
        documents = retriever.invoke(search_query)
        context = "\n\n".join([doc.page_content for doc in documents])

        # Build prompt with chat history support
        qa_prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a legal AI Assistant. Use the following context to answer the question.

IMPORTANT: If the question or context refers to old provisions such as the Indian Penal Code (IPC), Code of Criminal Procedure (CrPC), or Indian Evidence Act (IEA), you MUST mention both the old and new provisions side by side:
- IPC → Bharatiya Nyaya Sanhita (BNS)
- CrPC → Bharatiya Nagrik Suraksha Sanhita (BNSS)
- IEA → Bharatiya Sakshya Adhiniyam (BSA)

If the query is scenario-based, provide a detailed answer considering the facts and legal consequences.

Format rules (must follow strictly):
- Use valid GitHub-flavored Markdown
- Do NOT write content in a single long line
- Wrap text at logical sentence boundaries
- Always separate paragraphs with a blank line
- Use bullet points instead of inline lists"""),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("user", "Document Context:\n{context}"),
            ("user", "{question}"),
        ])

        chain = qa_prompt | llm
        response = chain.invoke({
            "context": context,
            "question": question,
            "chat_history": chat_history
        })

        # Token count
        total_tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            total_tokens = response.usage_metadata.get("total_tokens", 0)

        final_answer = run_output_guardrails(response.content, "Scenario")

        # Save chat history to local JSON file
        save_pdf_chat_history(unique_string, question, response.content, all_chats)

        return {
            "status": True,
            "answer": final_answer,
            "total_tokens_consumed": total_tokens,
            "filenames": filenames,
            "upload_date": upload_date,
            "file_count": file_count,
            "uniqueString": unique_string
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=f"Chat failed: {str(e)}")


# ============================================================================
# CLEANUP ENDPOINT (Optional - for maintenance)
# ============================================================================

@router.delete("/cleanup/{uniqueString}")
async def cleanup_temp_files(uniqueString: str):
    """
    Manually cleanup temporary uploaded files for a given uniqueString.
    Useful if processing fails or user cancels.
    """
    try:
        temp_dir = get_temp_upload_path(uniqueString)
        
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            return {
                'status': True,
                'message': f'Temporary files cleaned up for {uniqueString}'
            }
        else:
            return {
                'status': False,
                'message': f'No temporary files found for {uniqueString}'
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {str(e)}")