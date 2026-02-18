from langchain_community.embeddings import HuggingFaceEmbeddings

# Shared embedding model for PDF Q&A (loaded once, used by both mainqa.py and mainqa_test.py)
qa_embeddings = HuggingFaceEmbeddings(
    model_name="./models/all-MiniLM-L6-v2",
    model_kwargs={"device": "cpu"}
)
