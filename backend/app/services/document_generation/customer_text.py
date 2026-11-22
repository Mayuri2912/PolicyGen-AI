"""Remove drafting provenance from exports without censoring AI governance subject matter."""
import re

_GENERATION = re.compile(
    r"AI[- ]assisted (?:draft|revision)|(?:generated|written|drafted|produced) (?:by|using|with) "
    r"(?:PolicyGen|an? AI|an? (?:large )?(?:language|AI) model)|PolicyGen AI|(?:OpenRouter|openrouter)/|"
    r"Initial draft generated from the analysis|SIMULATED OUTPUT|mock mode|(?:review|approve|check) (?:the |this )?AI (?:output|draft)",
    re.IGNORECASE,
)
_APPENDIX = re.compile(r"\b(?:see|refer to|in|from) (?:the )?[^.!?\n]{0,60}\b(?:appendix|appendices|annex)\b", re.IGNORECASE)


def clean_customer_text(text: str) -> str:
    return " ".join(s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", text)
                    if s.strip() and not _GENERATION.search(s) and not _APPENDIX.search(s))


def clean_elements(elements: list[dict]) -> list[dict]:
    result = []
    for original in elements:
        block = {**original}
        kind = block["type"]
        if kind in ("paragraph", "subheading", "note"):
            block["text"] = clean_customer_text(block.get("text", ""))
            if not block["text"]:
                continue
        elif kind in ("bullets", "numbered"):
            block["items"] = [clean_customer_text(i) for i in block["items"]]
            block["items"] = [i for i in block["items"] if i]
            if not block["items"]:
                continue
        elif kind == "statements":
            block["statements"] = [{**s, "text": clean_customer_text(s["text"])} for s in block["statements"]]
            block["statements"] = [s for s in block["statements"] if s["text"]]
            if not block["statements"]:
                continue
        elif kind == "table":
            block["columns"] = [clean_customer_text(c) for c in block["columns"]]
            block["rows"] = [[clean_customer_text(c) for c in r] for r in block["rows"]]
            block["rows"] = [r for r in block["rows"] if any(r)]
        result.append(block)
    return result
