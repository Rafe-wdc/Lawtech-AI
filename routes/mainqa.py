from fastapi import APIRouter, UploadFile, File, Form, Request, Header, HTTPException
import fitz
import os
import logging

logger = logging.getLogger(__name__)
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from utils.rewrite_query import rewrite_query_with_context
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from typing import List, Optional
from werkzeug.utils import secure_filename
import tempfile
import time
from utils.text_extraction import extract_text_with_mask_from_pdf_with_gemini
from utils.guardrails import run_input_guardrails, run_output_guardrails
from utils.pdf_chat_history import load_pdf_chat_history, save_pdf_chat_history
from utils.embeddings import qa_embeddings as qa_instructor_embeddings

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

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

router = APIRouter(prefix="/pyapi", tags=["mainqa"])

@router.post("/mainqa")
async def mainqa(
    request: Request,
    usecase: str = Header(None),
    file: Optional[List[UploadFile]] = File(None),
    uniqueString: str = Form(None),
    question: Optional[str] = Form(None),
):
    try:
        # usecase = request.headers.get('usecase')
        logger.info("Usecase: %s", usecase)

        if usecase == 'upload':
            start_time = time.time()
            unique_string = uniqueString

            # 🛡️ Validation
            if not unique_string:
                return {'status': False, 'error': 'uniqueString is required'}
            if not file or all(filex.filename == '' for filex in file):
                return {'status': False, 'error': 'No selected files'}

            documents = []
            processed_filenames = []  # ✨ Track successfully processed filenames

            for filex in file:
                if filex and filex.filename.lower().endswith('.pdf'):
                    filename = secure_filename(filex.filename)

                    try:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                            filex.file.seek(0)
                            tmp_pdf.write(await filex.read())
                            tmp_pdf_path = tmp_pdf.name

                        pdf_doc = fitz.open(tmp_pdf_path)
                        num_pages = len(pdf_doc)
                        TEXT_LENGTH_THRESHOLD = num_pages * 700
                        logger.info(f"PDF has {num_pages} pages; using TEXT_LENGTH_THRESHOLD = {TEXT_LENGTH_THRESHOLD}")

                        extracted_text = ""
                        for i in range(num_pages):
                            page = pdf_doc.load_page(i)
                            text = page.get_text("text")
                            extracted_text += text + "\n"
                        pdf_doc.close()
                        logger.debug(f"Extracted text length from {filename}: {len(extracted_text.strip())} characters")

                        if len(extracted_text.strip()) < TEXT_LENGTH_THRESHOLD:
                            logger.warning(f"Low or no meaningful text found in {filename}, using Vision GPT fallback")
                            pdf_text = extract_text_with_mask_from_pdf_with_gemini(tmp_pdf_path, GOOGLE_API_KEY)
                            logger.debug(f"Vision GPT extracted text length: {len(pdf_text.strip())} characters")
                        else:
                            logger.info(f"Text length OK from {filename}, skipping Vision GPT")
                            pdf_text = extracted_text

                        if pdf_text.strip():
                            doc = Document(page_content=pdf_text, metadata={'source': filename})
                            documents.append(doc)
                            processed_filenames.append(filename)  # ✨ Add to list
                        else:
                            logger.warning(f"No text could be extracted from {filename}, even with fallback.")

                    except Exception as e:
                        logger.error(f"Error processing {filename}: {e}")

            if not documents:
                return {'status': False, 'error': 'No text extracted from PDFs'}

            logger.info("Documents processed: %d", len(documents))

            # Chunking
            split_start = time.time()
            text_splitter = RecursiveCharacterTextSplitter(separators=[""], chunk_size=15000, chunk_overlap=200)
            texts = text_splitter.split_documents(documents)
            logger.info("Chunks created: %d", len(texts))
            logger.debug("Chunking time: %s sec", round(time.time() - split_start, 2))

            # Chroma DB setup
            collection_name = f"collection_{unique_string}"
            persist_dir = _safe_persist_dir(unique_string)
            os.makedirs(persist_dir, exist_ok=True)

            logger.info("Creating Chroma DB...")

            # ✨ Check if collection already exists
            existing_filenames = []
            collection_exists = os.path.exists(os.path.join(persist_dir, 'chroma.sqlite3'))

            if collection_exists:
                logger.debug("DEBUG: Collection exists, loading existing collection...")
                # Load existing collection
                vectordb = Chroma(
                    embedding_function=qa_instructor_embeddings,
                    collection_name=collection_name,
                    persist_directory=persist_dir
                )
                
                # Get existing metadata BEFORE adding new documents
                try:
                    existing_metadata = vectordb._collection.metadata
                    if existing_metadata:
                        existing_filenames_str = existing_metadata.get("filenames", "")
                        existing_filenames = existing_filenames_str.split(",") if existing_filenames_str else []
                        existing_filenames = [f for f in existing_filenames if f]
                        logger.debug(f"DEBUG: Found existing filenames: {existing_filenames}")
                    else:
                        logger.debug("DEBUG: No metadata found in existing collection")
                except Exception as e:
                    logger.debug(f"DEBUG: Could not load existing metadata: {e}")
                
                # ✨ Add new documents to existing collection
                vectordb.add_documents(texts)
                logger.debug("DEBUG: Added documents to existing collection")
                
            else:
                logger.debug("DEBUG: Creating new collection...")
                # Create new collection
                vectordb = Chroma.from_documents(
                    documents=texts,
                    embedding=qa_instructor_embeddings,
                    collection_name=collection_name,
                    persist_directory=persist_dir
                )

            # Combine old + new filenames
            all_filenames = list(set(existing_filenames + processed_filenames))
            all_filenames = [f for f in all_filenames if f]

            logger.debug(f"DEBUG: Existing filenames: {existing_filenames}")
            logger.debug(f"DEBUG: New filenames: {processed_filenames}")
            logger.debug(f"DEBUG: Combined filenames: {all_filenames}")

            # ✨ Store combined filenames
            vectordb._collection.modify(
                metadata={
                    "filenames": ",".join(all_filenames),
                    "upload_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "file_count": str(len(all_filenames))
                }
            )


            # ✨✨ CRITICAL: Force immediate persistence
            try:
                # Method 1: Try to persist the client (works in some ChromaDB versions)
                if hasattr(vectordb._client, 'persist'):
                    vectordb._client.persist()
                    logger.debug("DEBUG: Forced client persist")
                
                # Method 2: Access the collection's internal persist (more reliable)
                if hasattr(vectordb._collection, '_client'):
                    vectordb._collection._client.persist()
                    logger.debug("DEBUG: Forced collection persist")
                
                # Method 3: Small delay to ensure write completes (fallback)
                time.sleep(0.2)
                logger.debug("DEBUG: Waited for persist")
                
                # Verify metadata was written
                check_metadata = vectordb._collection.metadata
                if check_metadata and check_metadata.get("filenames"):
                    logger.debug(f"DEBUG: VERIFIED metadata persisted: {check_metadata.get('filenames')}")
                else:
                    logger.warning("DEBUG: Metadata verification failed!")
                    
            except Exception as e:
                logger.debug(f"DEBUG: Persist attempt: {e}")

            logger.info("Chroma DB saved in: %s", persist_dir)
            logger.info(f"Stored filenames: {all_filenames} (Total: {len(all_filenames)})")

            total_time = round(time.time() - start_time, 2)
            return {
                'status': True,
                'chunks': len(texts),
                'filenames': all_filenames,
                'upload_time_sec': total_time
            }
        elif usecase == 'qa':
            # Parse JSON body
            body = await request.json()
            unique_string = uniqueString or body.get("uniqueString")
            question = question or body.get("question")

            # 🛡️ Validate
            if not unique_string:
                return {'error': 'uniqueString is required'}
            if not question:
                return {'error': 'question is required'}

            # Input guardrails
            guardrail_result = run_input_guardrails(question)
            if guardrail_result.blocked:
                return {'answer': guardrail_result.reason, 'filenames': [], 'upload_date': 'N/A'}

            # Load chat history from local JSON file
            chat_history, all_chats = load_pdf_chat_history(unique_string)

            # Rewrite query with conversational context for better retrieval
            search_query = rewrite_query_with_context(question, chat_history)

            collection_name = f"collection_{unique_string}"
            persist_dir = _safe_persist_dir(unique_string)
            logger.info("Loading Chroma DB from: %s", persist_dir)

            vectordb = Chroma(
                embedding_function=qa_instructor_embeddings,
                collection_name=collection_name,
                persist_directory=persist_dir
            )

            # ✨ Retrieve filenames from collection metadata
            try:
                metadata = vectordb._collection.metadata
                filenames_str = metadata.get("filenames", "")
                filenames = filenames_str.split(",") if filenames_str else []
                upload_date = metadata.get("upload_date", "Unknown")
                logger.debug(f"Loaded filenames: {filenames}")
                logger.debug(f"Upload date: {upload_date}")
            except Exception as e:
                logger.warning(f"Could not retrieve metadata: {e}")
                filenames = []
                upload_date = "Unknown"

            llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0.3)

            retriever = vectordb.as_retriever(search_kwargs={"k": 10})

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

            # Output guardrails: sanitize markdown + add disclaimer
            result = run_output_guardrails(response.content, "Scenario")

            # Save chat history to local JSON file
            save_pdf_chat_history(unique_string, question, response.content, all_chats)

            return {
                "answer": result,
                "filenames": filenames,
                "upload_date": upload_date
            }
        else:
            raise HTTPException(status_code=400, detail="Invalid usecase")

    except Exception as e:
        logger.error(f"An error occurred: {e}")
        raise HTTPException(status_code=500, detail=str(e))
