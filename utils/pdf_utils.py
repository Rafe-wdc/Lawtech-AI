import os
import pikepdf
import subprocess
import tempfile


def compress_pdf(input_path: str) -> str:
    """
    Compress PDF using PikePDF (lossless) and Ghostscript fallback (lossy).
    Returns path of the best compressed file.
    """

    original_size = os.path.getsize(input_path)
    print(f"[PDF] Original size: {original_size} bytes")

    # -----------------------------------------
    # Step 1: Try PikePDF (lossless compression)
    # -----------------------------------------
    pike_out = input_path.replace(".pdf", "_pike.pdf")
    try:
        with pikepdf.open(input_path) as pdf:
            pdf.save(
                pike_out,
                compress_streams=True
            )
        pike_size = os.path.getsize(pike_out)
        print(f"[PDF] PikePDF size: {pike_size} bytes")
    except Exception as e:
        print("[PDF] PikePDF failed:", e)
        pike_out = None
        pike_size = float("inf")

    # If PikePDF gives good compression → return it
    if pike_out and pike_size < original_size * 0.97:
        print("[PDF] Using PikePDF result")
        return pike_out

    # -----------------------------------------
    # Step 2: Ghostscript fallback (lossy)
    # -----------------------------------------
    gs_out = input_path.replace(".pdf", "_gs.pdf")

    try:
        gs_cmd = [
            "gs",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/ebook",       # good balance
            "-dNOPAUSE",
            "-dQUIET",
            "-dBATCH",
            f"-sOutputFile={gs_out}",
            input_path,
        ]
        subprocess.run(gs_cmd, check=True)
        gs_size = os.path.getsize(gs_out)
        print(f"[PDF] Ghostscript size: {gs_size} bytes")
    except Exception as e:
        print("[PDF] Ghostscript failed:", e)
        gs_out = None
        gs_size = float("inf")

    # -----------------------------------------
    # Final choice
    # -----------------------------------------
    compressed_path = None

    if gs_out and gs_size < pike_size:
        print("[PDF] Using Ghostscript result")
        compressed_path = gs_out
        if pike_out: os.remove(pike_out)
    elif pike_out:
        print("[PDF] Using PikePDF result (fallback)")
        compressed_path = pike_out
        if gs_out: os.remove(gs_out)
    else:
        print("[PDF] No compression possible")
        compressed_path = input_path

    return compressed_path
