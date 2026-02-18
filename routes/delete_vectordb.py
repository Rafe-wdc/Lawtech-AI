from fastapi import APIRouter, HTTPException
import os
import time
import shutil
import gc
from werkzeug.utils import secure_filename

router = APIRouter(prefix="/pyapi", tags=["delete_vectordb"])
vectordb_instances = {}

CHROMA_STORE_ROOT = os.path.realpath('./chroma_store')

@router.delete("/delete_vectordb/{unique_string}")
def delete_vectordb(unique_string: str):
    try:
        safe_name = secure_filename(unique_string)
        if not safe_name:
            raise HTTPException(status_code=400, detail="Invalid unique_string")
        persist_dir = os.path.realpath(os.path.join(CHROMA_STORE_ROOT, safe_name))
        if not persist_dir.startswith(CHROMA_STORE_ROOT):
            raise HTTPException(status_code=400, detail="Invalid unique_string")
        print("Trying to delete directory:", persist_dir)

        # --- Step 1: Check and remove from any global reference ---
        if unique_string in vectordb_instances:
            print("Releasing Chroma instance...")
            del vectordb_instances[unique_string]
            gc.collect()  # Force garbage collection

        # --- Step 2: Retry deletion (handle file lock) ---
        if os.path.exists(persist_dir):
            for attempt in range(5):
                try:
                    shutil.rmtree(persist_dir)
                    print("Directory deleted successfully.")
                    return {
                        'message': f'VectorDB for unique string "{unique_string}" successfully deleted.'
                    }
                except Exception as e:
                    print(f"Attempt {attempt+1}: Failed to delete. Retrying... ({e})")
                    time.sleep(1)
            # If still failing after retries
            raise HTTPException(status_code=500, detail=f"Failed to delete VectorDB {unique_string}")
        else:
            raise HTTPException(status_code=404, detail=f"No vectordb found for {unique_string}")

    except Exception as e:
        print(f"An error occurred while deleting the vectordb: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    