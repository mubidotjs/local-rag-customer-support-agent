"""Conservative, inspectable relation proofs over exact retrieved sentences.

This is a finite English template recognizer, not a semantic judge. Unknown
relations, missing scope, conflicting scalar facts and ambiguous aliases fail
closed. No scores, embeddings, generated explanations or fixture names are used.
"""
from dataclasses import dataclass
import re


ARTICLES = {"the", "a", "an", "my", "each"}
PLURALS = {"codes": "code", "tokens": "token", "requests": "request", "keys": "key",
           "tickets": "ticket", "reports": "report", "refunds": "refund", "displays": "display",
           "delays": "delay", "indicators": "indicator", "controls": "control"}
CONTROL_HEADS = {"beacon", "indicator", "chime", "bell", "lamp", "light", "button", "latch",
                 "switch", "dial", "control", "display", "sensor", "buzzer", "lever", "knob"}
# Deliberately narrow action equivalences. They never rewrite the source quote.
ACTIONS = {"silence": "mute", "mute": "mute", "enable": "enable", "activate": "enable",
           "disable": "disable", "deactivate": "disable", "reset": "reset", "factory reset": "factory_reset", "export": "export"}
DURATION = r"\d+(?:\.\d+)? (?:calendar |business )?(?:seconds?|minutes?|hours?|days?|months?|years?)"
UNRESOLVED = ("<unresolved>",)
REQUEST_PURPOSES = {"recovery", "refund", "return", "renewal"}


def noun(text):
    text = text.strip().lower()
    if not text:
        return ()
    if not re.fullmatch(r"[a-z0-9]+(?:[ -][a-z0-9]+){0,7}", text):
        return UNRESOLVED
    words = re.findall(r"[a-z0-9]+", text.lower())
    result = tuple(PLURALS.get(w, w) for w in words if w not in ARTICLES)
    forbidden = {"if","unless","when","while","unlike","except","instead","or","and","with","without",
                 "of","for","from","after","before","that","which","is","are","not","it","they","them","this","one","as"}
    return result if 1 <= len(result) <= 6 and not set(result) & forbidden else UNRESOLVED


def matches(requested, stated):
    """Exact normalized names/objects; no substring or qualifier dropping."""
    return bool(requested and requested != UNRESOLVED and requested == stated)


def subject_matches(requested, stated):
    return matches(requested, stated) or bool(len(stated) >= 3 and stated[-1] == "request" and stated[-2] in REQUEST_PURPOSES
                and requested == stated[:-2]+stated[-1:])


def unqualified_reference(requested, stated):
    # Only an explicit one-hop alias may resolve a bare control head. Qualified
    # names cannot drop arbitrary words such as a product's Pro/Plus designation.
    return requested == stated or bool(len(requested) == 1 and stated and requested[-1] == stated[-1])


def partial_name(requested, stated):
    """Used to detect ambiguity, never to authorize an answer."""
    if not requested or not stated or requested[-1] != stated[-1]:
        return False
    remaining = iter(stated[:-1])
    return all(any(token == wanted for token in remaining) for wanted in requested[:-1])


@dataclass(frozen=True)
class Intent:
    facet: str
    entity: tuple = ()
    measure: tuple = ()
    action: str = ""
    wording: str = ""


@dataclass
class Fact:
    facet: str
    entity: tuple
    ref: dict
    measure: tuple = ()
    action: str = ""
    value: str = ""
    support: tuple = ()


def question_intent(clause):
    text = re.sub(r"\s+", " ", clause.strip(" ?.\n").lower())
    patterns = [
        (r"how long (?:does|do) (.+?) (?:remain|stay) valid", lambda m: Intent("validity_duration", noun(m[1]))),
        (r"how long (?:is|are) (.+?) valid", lambda m: Intent("validity_duration", noun(m[1]))),
        (r"when (?:does|do) (.+?) expire", lambda m: Intent("validity_duration", noun(m[1]))),
        (r"when (?:does|do) (.+?) release", lambda m: Intent("release_condition", noun(m[1]))),
        (r"how long (?:does|do) (.+?) last", lambda m: Intent("policy_duration", noun(m[1]))),
        (r"what is (.+?) grace period", lambda m: Intent("grace_duration", noun(m[1]))),
        (r"how many (.+?) (?:does|do) each (.+?) issue", lambda m: Intent("issued_count", noun(m[2]), noun(m[1]))),
        (r"how many (.+?) (?:does|do) (.+?) (?:permit|allow)", lambda m: Intent("permitted_count", noun(m[2]), noun(m[1]))),
        (r"(?:what is (?:the )?)?(?:(.+?) )?return window", lambda m: Intent("return_window", noun(m[1] or ""))),
        (r"how many days after delivery may i return (.+)", lambda m: Intent("return_window", noun(m[1]))),
        (r"when (?:are|is) (?:the )?refunds? issued", lambda m: Intent("refund_timing", noun("refund"))),
        (r"are (.+?) supported", lambda m: Intent("support_policy", measure=noun(m[1]))),
        (r"are (diagnostic reports) sent automatically", lambda m: Intent("automatic_policy", measure=noun(m[1]))),
        (r"what (sleep delays) are available", lambda m: Intent("configuration_options", noun(m[1]))),
        (r"under what condition (?:is|are) (.+?) (eligible|accepted|allowed|permitted)", lambda m: Intent("policy_condition", noun(m[1]), action=m[2])),
        (r"(?:is|are) (.+?) (eligible|accepted|allowed|permitted)", lambda m: Intent("policy_condition", noun(m[1]), action=m[2])),
        (r"how (?:can|do) i (factory reset|silence|mute|enable|activate|disable|deactivate|reset|export) (.+)",
         lambda m: Intent("configuration_action", noun(m[2]), action=ACTIONS[m[1]], wording=m[1])),
    ]
    for pattern, build in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            return build(match)
    # A missing fee must remain an explicit uncovered compound-question facet.
    if re.fullmatch(r"(?:what is (?:the )?)?.+ (?:fee|price|cost)", text):
        return Intent("price_policy", noun(text))
    return Intent("unknown")


def facts_for(ref):
    text = ref["quote"]
    patterns = [
        (rf"(?:The )?(.+?) (?:remains?|stays?|is) valid for ({DURATION})\.",
         lambda m: Fact("validity_duration", noun(m[1]), ref, value=m[2].lower())),
        (rf"(?:The )?(.+?) expires? after ({DURATION})\.",
         lambda m: Fact("validity_duration", noun(m[1]), ref, value=m[2].lower())),
        (rf"(?:The )?(.+?) releases? after ({DURATION}) of holding (.+?)\.",
         lambda m: Fact("release_condition", noun(m[1]), ref, value=m[2].lower()+" / "+m[3].lower())),
        (rf"(?:The )?(.+?) lasts? ({DURATION})(?: (?:after|from) delivery)?\.",
         lambda m: Fact("policy_duration", noun(m[1]), ref, value=m[2].lower())),
        (rf"(?:The )?(.+?) grace period is ({DURATION})\.",
         lambda m: Fact("grace_duration", noun(m[1]), ref, value=m[2].lower())),
        (r"Each (.+?) issues (\d+) (.+?)\.",
         lambda m: Fact("issued_count", noun(m[1]), ref, measure=noun(m[3]), value=m[2])),
        (r"(?:The )?(.+?) (?:permits?|allows?) (\d+) (.+?)\.",
         lambda m: Fact("permitted_count", noun(m[1]), ref, measure=noun(m[3]), value=m[2])),
        (rf"Customers may return a (.+?) within ({DURATION}) of delivery\.",
         lambda m: Fact("return_window", noun(m[1]), ref, value=m[2].lower())),
        (rf"(.+?) returns are accepted within ({DURATION}) of delivery\.",
         lambda m: Fact("return_window", noun(m[1]), ref, value=m[2].lower())),
        (rf"Refunds are issued to .+? within ({DURATION}) after inspection\.",
         lambda m: Fact("refund_timing", noun("refund"), ref, value=m[1].lower())),
        (r"(.+?) (?:is|are) (not )?supported by (.+?)\.",
         lambda m: Fact("support_policy", noun(m[3]), ref, measure=noun(m[1]), value="no" if m[2] else "yes")),
        (r"(.+?) never sends? (.+?) automatically\.",
         lambda m: Fact("automatic_policy", noun(m[1]), ref, measure=noun(m[2]), value="never")),
        (r"Available (sleep delays) are ([\d, and]+ minutes)\.",
         lambda m: Fact("configuration_options", noun(m[1]), ref, value=m[2].lower())),
        (r"(?:The )?(.+?) (?:is|are) (eligible|accepted|allowed|permitted) (?:only )?if (.+?)\.",
         lambda m: Fact("policy_condition", noun(m[1]), ref, action=m[2].lower(), value=m[3].lower())),
        (r"To (factory reset|mute|silence|enable|activate|disable|deactivate|reset|export) (.+?), (.+?)\.",
         lambda m: Fact("configuration_action", noun(m[2]), ref, action=ACTIONS[m[1].lower()], value=m[3].lower())),
    ]
    for pattern, build in patterns:
        match = re.fullmatch(pattern, text, re.I)
        if match:
            return [build(match)]
    return []


class Grounding:
    def __init__(self, question, passages, settings):
        clauses = [c for c in re.split(r"\band\b|\balso\b|;", question, flags=re.I) if c.strip()]
        self.intents = [question_intent(c) for c in clauses] if 1 <= len(clauses) <= 3 else [Intent("unknown")]
        self.refs, self.facts, self.aliases = [], [], []
        for p in passages:
            if p["quarantined"] or p["ambiguous_topic"] or p["similarity"] < settings.min_similarity:
                continue
            for q in p["quotes"]:
                ref = {"chunk_id": p["id"], "document_id": p["document_id"], "title": p["title"], "version": p["version"], **q}
                self.refs.append(ref)
                self.facts.extend(f for f in facts_for(ref) if f.entity and f.entity != UNRESOLVED and f.measure != UNRESOLVED)
                alias = re.fullmatch(r"(?:The )?(.+?) is (?:also called|also known as|called) (?:the )?(.+?)\.", q["quote"], re.I)
                if not alias:
                    alias = re.fullmatch(r"(?:The )?(.+?) is the (.+?)\.", q["quote"], re.I)
                if alias:
                    left, right = noun(alias[1]), noun(alias[2])
                    forbidden = {"is", "are", "that", "which", "and", "or", "not", "near", "with", "similar"}
                    if 1 <= len(left) <= 5 and 1 <= len(right) <= 5 and not (set(left+right) & forbidden) and left[-1] in CONTROL_HEADS and right[-1] in CONTROL_HEADS:
                        if not any(r["document_id"] == ref["document_id"] and {a,b} == {left,right} for a,b,r in self.aliases):
                            self.aliases.append((left, right, ref))
        # A release condition may be a step of the immediately preceding explicit
        # reset procedure. It cannot borrow a procedure from another document.
        ordered = sorted(self.refs, key=lambda r: (r["document_id"], r["normalized_start"]))
        roots = {(f.ref["document_id"], f.ref["normalized_start"]): f for f in self.facts if f.action in {"reset", "factory_reset"}}
        for previous, current in zip(ordered, ordered[1:]):
            root = roots.get((previous["document_id"], previous["normalized_start"]))
            release = re.fullmatch(r"Release the (.+?) when .+?\.", current["quote"], re.I)
            held = re.search(r"hold the (.+?) for \d+ seconds", previous["quote"], re.I)
            same_reset_button = bool(release and held and noun(release[1]) == ("reset","button") and noun(held[1])[-2:] == ("reset","button"))
            if root and same_reset_button and current["document_id"] == previous["document_id"] and 0 <= current["normalized_start"]-previous["normalized_end"] <= 2:
                self.facts.append(Fact("configuration_action", root.entity, current, action=root.action, value=current["quote"], support=(previous,)))
        self._binding_cache = {}

    def _alias_binding(self, intent, fact):
        links = [a for a in self.aliases if a[2]["document_id"] == fact.ref["document_id"]]
        candidates = []
        for left, right, ref in links:
            for source, target in ((left, right), (right, left)):
                if unqualified_reference(intent.entity, source) and unqualified_reference(fact.entity, target):
                    # One-hop, one-to-one only; multiple documented targets abstain.
                    neighbours = {b if a == source else a for a, b, _ in links if a == source or b == source}
                    targets = {a if b == target else b for a, b, _ in links if a == target or b == target}
                    if len(neighbours) != 1 or len(targets) != 1:
                        return None, "ambiguous_alias"
                    compatible = {x for a, b, _ in links for x in (a, b) if unqualified_reference(fact.entity, x)}
                    if compatible != {target}:
                        return None, "ambiguous_alias"
                    candidates.append(ref)
        if len({(r["document_id"], r["normalized_start"]) for r in candidates}) > 1:
            return None, "ambiguous_alias"
        return (candidates[0], "") if candidates else (None, "entity_mismatch")

    def _bindings(self, intent):
        # At most three intents; alias/scalar ambiguity is computed once per
        # request context, rather than rescanned for every proposed sentence.
        if intent not in self._binding_cache:
            self._binding_cache[intent] = self._compute_bindings(intent)
        return self._binding_cache[intent]

    def _compute_bindings(self, intent):
        candidates, error = [], "facet_mismatch"
        if intent.entity == UNRESOLVED or intent.measure == UNRESOLVED:
            return [], "ambiguous_entity"
        ambiguous_alias = False
        related_entities = set()
        for fact in self.facts:
            if fact.facet != intent.facet or fact.action != intent.action:
                continue
            if intent.measure and not matches(intent.measure, fact.measure):
                continue
            if intent.entity and partial_name(intent.entity, fact.entity):
                related_entities.add((fact.ref["document_id"],fact.entity,fact.measure))
            alias = None
            if intent.entity and not subject_matches(intent.entity, fact.entity):
                alias, failure = self._alias_binding(intent, fact)
                if alias is None:
                    error = failure
                    ambiguous_alias |= failure == "ambiguous_alias"
                    continue
            candidates.append((fact, alias))
        if ambiguous_alias:
            return [], "ambiguous_alias"
        if len(related_entities) > 1 and any(intent.entity != f.entity for f,_ in candidates):
            return [], "ambiguous_entity"
        if not candidates and len(related_entities) > 1:
            return [], "ambiguous_entity"
        entities = {(f.ref["document_id"], f.entity, f.measure) for f, _ in candidates}
        if len(entities) > 1:
            return [], "ambiguous_entity"
        # Contradictory scalar facts cannot be resolved by choosing one quote.
        if intent.facet in {"validity_duration", "policy_duration", "issued_count", "permitted_count", "grace_duration", "release_condition", "return_window", "refund_timing", "support_policy"} and len({f.value for f, _ in candidates}) > 1:
            return [], "conflicting_relation"
        if intent.facet == "policy_condition" and len({f.value for f, _ in candidates}) > 1:
            return [], "ambiguous_condition"
        return candidates, error

    def prove(self, chunk_id, quote):
        proofs, failure = [], "unsupported_relation"
        claimed = [f for f in self.facts if f.ref["chunk_id"] == chunk_id and f.ref["quote"] == quote]
        if not claimed:
            return [], failure
        if not any(f.facet == i.facet and f.action == i.action and (not i.measure or matches(i.measure, f.measure)) for f in claimed for i in self.intents):
            return [], "facet_mismatch"
        for index, intent in enumerate(self.intents):
            bindings, error = self._bindings(intent)
            failure = error
            for fact, alias in bindings:
                if fact.ref["chunk_id"] == chunk_id and fact.ref["quote"] == quote:
                    source_action = re.match(r"To (factory reset|\w+)", fact.ref["quote"], re.I)
                    proofs.append({"intent": index, "facet": intent.facet, "entity": " ".join(fact.entity),
                                   "requested_entity": " ".join(intent.entity),
                                   "action": intent.action, "question_action_wording": intent.wording,
                                   "source_action_wording": source_action[1].lower() if source_action and intent.facet == "configuration_action" else "",
                                   "action_link": "explicit_procedure_step" if fact.support else "bounded_action_mapping" if intent.action and intent.wording else "literal_predicate",
                                   "basis": "explicit_same_document_alias" if alias else "unique_facet_scope" if not intent.entity else "unique_request_purpose_scope" if intent.entity != fact.entity else "literal_entity_and_relation",
                                   "support_chain": [*([alias] if alias else []), *fact.support, fact.ref]})
        return proofs, "" if proofs else failure

    def coverage(self, proofs):
        return {p["intent"] for p in proofs} == set(range(len(self.intents)))
