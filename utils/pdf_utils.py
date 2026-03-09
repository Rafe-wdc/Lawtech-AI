import os
import pikepdf
import subprocess
import tempfile
import logging

logger = logging.getLogger(__name__)


def compress_pdf(input_path: str) -> str:
    """
    Compress PDF using PikePDF (lossless) and Ghostscript fallback (lossy).
    Returns path of the best compressed file.
    """

    original_size = os.path.getsize(input_path)
    logger.info("[PDF] Original size: %d bytes", original_size)

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
        logger.info("[PDF] PikePDF size: %d bytes", pike_size)
    except Exception as e:
        logger.error("[PDF] PikePDF failed: %s", e)
        pike_out = None
        pike_size = float("inf")

    # If PikePDF gives good compression → return it
    if pike_out and pike_size < original_size * 0.97:
        logger.info("[PDF] Using PikePDF result")
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
        logger.info("[PDF] Ghostscript size: %d bytes", gs_size)
    except Exception as e:
        logger.error("[PDF] Ghostscript failed: %s", e)
        gs_out = None
        gs_size = float("inf")

    # -----------------------------------------
    # Final choice
    # -----------------------------------------
    compressed_path = None

    if gs_out and gs_size < pike_size:
        logger.info("[PDF] Using Ghostscript result")
        compressed_path = gs_out
        if pike_out: os.remove(pike_out)
    elif pike_out:
        logger.info("[PDF] Using PikePDF result (fallback)")
        compressed_path = pike_out
        if gs_out: os.remove(gs_out)
    else:
        logger.warning("[PDF] No compression possible")
        compressed_path = input_path

    return compressed_path
