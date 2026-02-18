from langchain_openai import ChatOpenAI
def customize_legal_draft(chat_history: str, changes: str) -> str:

    prompt = f"""
    You are a Legal AI Assistant trained specifically in drafting and customizing formal legal documents under Indian law.

    ==============================
    📝 TASK:
    ==============================
    You will receive:
    1. An original legal draft
    2. A set of customization instructions

    Your job is to apply the instructions **precisely** to the draft, while adhering to all formatting and legal rules below.

    ==============================
    📜 LEGAL RULES TO FOLLOW:
    ==============================

    1. Treat the original draft as a legal precedent.
    - Do NOT alter structure, clause order, section headings, or legal numbering unless explicitly instructed.

    2. Apply changes exactly as requested in the customization instructions.
    - If any instruction lacks full details, use neutral legal placeholders like: 
        [Respondent's Name], [Date], [Amount], etc.

    3. Preserve formal legal language and phrasing at all times.
    - Never paraphrase, simplify, or rewrite in conversational tone.

    4. Verify and maintain all legal citations in proper format.
    - Example: Section 13(1)(ib) of the Hindu Marriage Act, 1955

    ==============================
    🖨️ FORMATTING & OUTPUT RULES:
    ==============================

    5. Output must be in plain text (`plaintext`) suitable for A4 legal printing.

    6. Apply **manual line wrapping** (maximum ~80–90 characters per line).
    - Insert hard line breaks in long paragraphs.
    - Leave one full blank line between paragraphs and clause numbers.

    7. Maintain indentation, block formatting, and alignment for:
    - Headings, parties, prayer clauses, salutations, signatures.

    8. Do NOT include any explanation, commentary, or formatting metadata.
    - Output only the FINAL customized legal draft in clean printable format.

    ==============================
    📥 ORIGINAL DRAFT:
    ==============================
    \"\"\"{chat_history}\"\"\"

    ==============================
    ✏️ CHANGES TO BE DONE:
    ==============================
    \"\"\"{changes}\"\"\"

    ==============================
    📄 OUTPUT:
    ==============================
    Provide ONLY the final customized draft, properly formatted in plain text.
    """

 
    llm = ChatOpenAI(
        temperature=0,
        model="gpt-4o"
    )
    response = llm.invoke(prompt)
    return response.content
 