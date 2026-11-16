"""Deterministic character offsets are Unicode code points, end exclusive."""
import re

HEADER = re.compile(r"\A---\nid: ([a-z0-9-]{1,60})\ntitle: ([^\n]{1,120})\nversion: ([^\n]{1,40})\ntopic: ([a-z0-9-]{1,60})\n---\n")


def normalize(source: str) -> tuple[str, list[tuple[int, int]]]:
    chars, offsets = [], []
    i = 0
    while i < len(source):
        start = i
        char = source[i]
        i += 1
        if char == "\ufeff" and start == 0:
            continue
        if char == "\r":
            if i < len(source) and source[i] == "\n":
                i += 1
            char = "\n"
        elif char == "\t":
            char = " "
        chars.append(char)
        offsets.append((start, i))
    return "".join(chars), offsets


def chunks(text: str, size: int = 800):
    if not 1 <= size <= 800:
        raise ValueError("Chunk size must be between 1 and 800")
    start = 0
    while start < len(text):
        while start < len(text) and text[start].isspace():
            start += 1
        if start == len(text):
            break
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
            if boundary > start + size // 2:
                end = boundary + 1
        while end > start and text[end - 1].isspace():
            end -= 1
        yield start, end, text[start:end]
        start = end


STOP = set("a an the is are was were be been being to of for in on at and or with can could do does how what when where why which i my me you your it its have has please tell about from by this that there many much will would should also".split())
STOP.update({"lumadock", "lumadesk", "home", "dock", "device"})


def terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP}


def sentences(text: str) -> list[str]:
    return [quote for _, _, quote in sentence_spans(text)]


def sentence_spans(text: str):
    """Parse full prose paragraphs before chunk slicing; preserve soft newlines."""
    def scan(a, b):
        block = text[a:b]
        start = 0
        for end in re.finditer(r"[.!?](?=\s|$)", block):
            segment = block[start:end.end()]
            quote = segment.strip()
            if len(quote) >= 15:
                absolute = a + start + len(segment) - len(segment.lstrip())
                yield absolute, absolute + len(quote), quote
            start = end.end()
    beginning, offset = None, 0
    for line in text.splitlines(keepends=True):
        if not line.strip() or line.lstrip().startswith("#"):
            if beginning is not None:
                yield from scan(beginning, offset)
                beginning = None
        elif beginning is None:
            beginning = offset
        offset += len(line)
    if beginning is not None:
        yield from scan(beginning, offset)


CONTROL = re.compile(
    r"(?:ignore|disregard|override).{0,100}(?:instructions?|rules?|system|evidence)"
    r"|(?:system|assistant|developer)\s*(?:prompt\b|message\b|:)"
    r"|</?(?:system|assistant|developer)>|fabricat\w*.{0,40}citation", re.I | re.S)


def has_instructions(text: str) -> bool:
    return bool(CONTROL.search(text))


def clauses_supported(question: str, quotes: list[str]) -> bool:
    """Necessary lexical coverage only; never a semantic entailment claim."""
    clauses = re.split(r"\b(?:and|also)\b|[;?]", question.lower())
    evidence = terms(" ".join(quotes))
    return all(not terms(clause) or bool(terms(clause) & evidence) for clause in clauses)


def answer_shape_matches(question: str, quote: str) -> bool:
    if re.search(r"\b(?:how many|how much|how long|price|cost|fee)\b", question, re.I):
        return bool(re.search(r"\b\d+(?:\.\d+)?\b|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten)\b", quote, re.I))
    return True
