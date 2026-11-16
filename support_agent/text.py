"""Deterministic character offsets are Unicode code points, end exclusive."""
import re


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
    # Quotes must be complete body sentences, never metadata or fragments.
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text)
            if len(s.strip()) >= 15 and s.strip().endswith((".", "!", "?"))]
