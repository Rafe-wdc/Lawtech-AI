"""A/B the REAL Vision OCR prompt across candidate models on real test images.

Scores with the project's OWN quality gates (_ocr_result_is_usable,
count_illegible_markers) so verdicts are comparable to production behaviour.
"""
import base64, json, os, sys, time, warnings
warnings.filterwarnings("ignore")
from concurrent.futures import ThreadPoolExecutor

ROOT = r"c:\Users\Admin\Downloads\Lawtech-AI\Lawtech-AI"
sys.path.insert(0, ROOT); os.chdir(ROOT)
from dotenv import load_dotenv; load_dotenv(".env")

from langchain.chat_models import init_chat_model
from core.file_processor import (
    _OCR_PROMPT_BODY, _ocr_result_is_usable, count_illegible_markers,
    _extract_ai_text,
)

IMAGES = [
    ("NDA scan (clean legal doc)", "non-disclosure-agreement-uplead-791x1024.jpg"),
    ("real_blur (blurred scan)",   "real_blur.png"),
    ("blurry image",               "blurry image.jpg"),
]

# Claude is included only when a credential is present. `claude-sonnet-5`
# REJECTS `temperature` (400) and `budget_tokens` (400) — omit both. Omitting
# `thinking` runs adaptive, which is the only on-mode for Sonnet 5.
# Requires: pip install langchain-anthropic
_CLAUDE = []
if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
    try:
        import langchain_anthropic  # noqa: F401
        _CLAUDE = [("claude-sonnet-5", "anthropic:claude-sonnet-5", {})]
    except ImportError:
        print("[skip] claude-sonnet-5: pip install langchain-anthropic")
else:
    print("[skip] claude-sonnet-5: no ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN")

MODELS = _CLAUDE + [
    ("gemini-2.5-flash (CURRENT)", "google_genai:gemini-2.5-flash", dict(temperature=0.0)),
    ("gemini-3.8-flash",           "google_genai:gemini-3.8-flash", dict(thinking_level="low")),
    ("gemini-3.5-flash",           "google_genai:gemini-3.5-flash", dict(thinking_level="low")),
    ("gemini-pro-latest",          "google_genai:gemini-pro-latest", dict(thinking_level="low")),
    ("gemini-3.1-pro-preview",     "google_genai:gemini-3.1-pro-preview", dict(thinking_level="low")),
    ("gpt-5.4 (openai)",           "openai:gpt-5.4", {}),
    ("gpt-5.5 (openai)",           "openai:gpt-5.5", {}),
]


def b64(path):
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode()


def run(label, spec, kwargs, img_label, img_path):
    t0 = time.time()
    try:
        llm = init_chat_model(spec, max_tokens=8192, max_retries=1, timeout=180, **kwargs)
        mime = "image/png" if img_path.lower().endswith(".png") else "image/jpeg"
        msg = [{"role": "user", "content": [
            {"type": "text", "text": _OCR_PROMPT_BODY},
            {"type": "image_url",
             "image_url": {"url": f"data:{mime};base64,{b64(img_path)}"}},
        ]}]
        text = _extract_ai_text(llm.invoke(msg)).strip()
        usable, reason = _ocr_result_is_usable(text)
        return dict(model=label, img=img_label, ok=True, chars=len(text),
                    usable=usable, reason=reason,
                    illegible=count_illegible_markers(text),
                    secs=round(time.time() - t0, 1), text=text)
    except Exception as e:
        return dict(model=label, img=img_label, ok=False,
                    err=f"{type(e).__name__}: {str(e)[:130]}",
                    secs=round(time.time() - t0, 1), text="")


if __name__ == "__main__":
    jobs = [(l, s, k, il, ip) for (l, s, k) in MODELS for (il, ip) in IMAGES]
    with ThreadPoolExecutor(max_workers=7) as ex:
        res = list(ex.map(lambda a: run(*a), jobs))
    out = (r"C:\Users\Admin\AppData\Local\Temp\claude"
           r"\c--Users-Admin-Downloads-Lawtech-AI"
           r"\ff30d7af-8feb-4e5c-b170-1f7641683f7d\scratchpad\vision_results.json")
    json.dump(res, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("WROTE", out, len(res))
