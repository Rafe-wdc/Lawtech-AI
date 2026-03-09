import textwrap
import tiktoken
import logging

logger = logging.getLogger(__name__)


def count_tokens(text: str, model: str = "gpt-4") -> int:
    """
    Count the number of tokens in the text for a given model.

    Args:
        text (str): The text to count tokens for.
        model (str): Model name.

    Returns:
        int: Number of tokens.
    """
    encoding = tiktoken.encoding_for_model(model)
    tokens = encoding.encode(text)
    return len(tokens)

def wrap_text_preserve_newlines(text: str, width: int = 110) -> str:
    """
    Wrap text while preserving newlines.

    Args:
        text (str): Input text.
        width (int): Line width.

    Returns:
        str: Wrapped text.
    """
    try:
        lines = text.split('\n')
        wrapped_lines = [textwrap.fill(line, width=width) for line in lines]
        wrapped_text = '\n'.join(wrapped_lines)
        return wrapped_text
    except Exception as e:
        logger.error("Error in wrap_text_preserve_newlines: %s", e)
        return {'error': str(e)}

def process_llm_response(llm_response: dict, query: str):
    """
    Print and process the LLM response.

    Args:
        llm_response (dict): The LLM response.
        query (str): The user query.

    Returns:
        None
    """
    try:
        logger.debug("LLM response: %s", wrap_text_preserve_newlines(llm_response['result']))
    except Exception as e:
        logger.error("Error in process_llm_response: %s", e)
        return {'error': str(e)}