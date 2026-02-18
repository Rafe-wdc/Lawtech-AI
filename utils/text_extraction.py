import fitz  # PyMuPDF
from openai import OpenAI
import io
import base64
OPENAI_BATCH_SIZE = 30
def extract_text_with_mask_from_pdf_pymupdf(pdf_path, api_key):
    client = OpenAI(api_key=api_key)
    doc = fitz.open(pdf_path)
 
    results = []
    batch = []
 
    def process_batch(batch, batch_start_page):
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"Extract all visible text and markdown tables from these document images (Pages {batch_start_page + 1} to {batch_start_page + len(batch)}). "
                                    "If anything is missing, blurred or illegible, replace it with most similar text or possible context."
                                ),
                            },
                            *[
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{b64_img}"}
                                }
                                for b64_img in batch
                            ],
                        ],
                    }
                ],
            )

            return [f"--- Pages {batch_start_page + 1}-{batch_start_page + len(batch)} ---\n{response.choices[0].message.content.strip()}"]

        except Exception as e:
            return [f"Error processing batch starting from page {batch_start_page + 1}: {e}"]

 
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=200)
        buffer = io.BytesIO()
        buffer.write(pix.tobytes("png"))
        img_data = buffer.getvalue()
        base64_img = base64.b64encode(img_data).decode("utf-8")
 
        batch.append(base64_img)
 
        if len(batch) == OPENAI_BATCH_SIZE:
            results.extend(process_batch(batch, i - len(batch) + 1))
            batch = []

    # Process any remaining pages
    if batch:
        results.extend(process_batch(batch, len(doc) - len(batch)))
 
    return "\n\n".join(results)


from langchain_google_genai import ChatGoogleGenerativeAI

GEMINI_BATCH_SIZE = 5

def extract_text_with_mask_from_pdf_with_gemini(pdf_path, google_api_key):
    # Initialize LangChain Gemini client
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-lite",
        google_api_key=google_api_key,
        temperature=0,   # keep deterministic for extraction
        max_output_tokens=2048,
    )

    results = []
    batch_images = []

    def process_batch(batch_b64, batch_start_page):
        # Build the message payload
        content = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Extract all visible text and markdown tables from these document images "
                            f"(Pages {batch_start_page + 1} to {batch_start_page + len(batch_b64)}). "
                            "If anything is missing, blurred or illegible, replace it with most similar text or possible context."
                        ),
                    },
                    *[
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64_img}"}
                        }
                        for b64_img in batch_b64
                    ],
                ],
            }
        ]

        # Call Gemini model through LangChain
        resp = llm.invoke(content)
        return f"--- Pages {batch_start_page + 1}-{batch_start_page + len(batch_b64)} ---\n{resp.content.strip()}"

    # Read PDF and convert pages to images
    doc = fitz.open(pdf_path)
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=120)
        buf = io.BytesIO()
        buf.write(pix.tobytes("png"))
        img_data = buf.getvalue()
        b64 = base64.b64encode(img_data).decode("utf-8")
        batch_images.append(b64)

        if len(batch_images) == GEMINI_BATCH_SIZE:
            results.append(process_batch(batch_images, i - len(batch_images) + 1))
            batch_images = []

    # Process leftover pages
    if batch_images:
        results.append(process_batch(batch_images, len(doc) - len(batch_images)))

    return "\n\n".join(results)
