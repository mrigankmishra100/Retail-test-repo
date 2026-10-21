import re

DISTRIBUTORS = ("BlueDart", "Delhivery", "Ekart", "DTDC")
ALIASES = {"BlueDart": r"\bblue\s*dart\b", "Delhivery": r"\bdelhivery\b", "Ekart": r"\be[\s-]*kart\b", "DTDC": r"\bdtdc\b"}


def remove_legacy_demo_prefix(answer: str) -> str:
    prefix = "Fictional demo data (not actual company policies):"
    return answer[len(prefix):].lstrip() if answer.startswith(prefix) else answer


def retrieval_queries(question: str) -> list[str]:
    names = [name for name, pattern in ALIASES.items() if re.search(pattern, question, re.I)]
    if not names and re.search(r"\b(which|all|compare|comparison|distributors|carriers)\b", question, re.I):
        names = list(DISTRIBUTORS)
    if not names:
        return [question]
    # Each search should target one company, not repeat all names from a comparison.
    topic = question
    for pattern in ALIASES.values():
        topic = re.sub(pattern, " ", topic, flags=re.I)
    topic = re.sub(r"\s+", " ", topic).strip()
    return [f"{name}: {topic}" for name in names]
