"""Finite entity/facet/alias proofs and frozen offline replay, with no model I/O."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import hashlib
import json
import pytest
from support_agent.answer import answer, verify_selection
from support_agent.config import Settings
from support_agent.relevance import Grounding
from evaluation.relevance.offline import frozen_holdout, holdout, passages_for_documents, preserved, replay


def validate(question, body, quote, *, identifier="fixture"):
    documents=[{"id":identifier,"title":"Fictional Support Note","version":"1.0","topic":"fixture","body":body}]
    passages=passages_for_documents(documents)
    return verify_selection({"abstain":False,"claims":[{"chunk_id":identifier+":c000","quote":quote}]},passages,question,Settings())


@pytest.mark.parametrize("case_id", [c["id"] for c in frozen_holdout()["cases"]])
def test_frozen_separately_authored_holdout(case_id):
    report=holdout()
    result=next(r for r in report["records"] if r["id"]==case_id)
    assert result["correct"], result
    assert report["model_requests"]==0 and report["real_model_status"]=="NOT RERUN"


def test_all_historical_decisions_are_replayed_without_contacting_a_model():
    report=replay()
    assert report==json.loads(Path("reports/offline-relevance-hardening/replay.json").read_text())
    assert holdout()==json.loads(Path("reports/offline-relevance-hardening/holdout.json").read_text())
    audit=json.loads(Path("reports/offline-relevance-hardening/audit.json").read_text())
    for name,digest in audit["validator_source_sha256"].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest
    assert report["cases"]==12 and report["previously_passing_cases_retained"]==9
    assert report["safe_rejections"]==2 and report["frozen_rubric_correct"]==10
    assert report["remaining_supported_false_abstentions"]==["late-amber","adjacent-cobalt"]
    assert report["unsupported_answers"]==[]
    assert report["model_requests"]==0 and all(not r["new_answer_generation_demonstrated"] for r in report["records"])
    assert preserved()==139


@pytest.mark.parametrize("case_id", ["late-amber","adjacent-cobalt"])
def test_correct_single_lifetime_is_accepted_but_original_mixed_output_is_not(case_id):
    historical=json.loads(Path("reports/phase-d-v2-once/report.json").read_text())
    record=next(r for r in historical["records"] if r["id"]==case_id)
    labels=json.loads(Path("evaluation/labels/v2/labels.json").read_text())
    case=next(c for c in labels["holdout"]["cases"] if c["id"]==case_id)
    claim={"chunk_id":case["evidence"][0]["chunk_id"],"quote":case["evidence"][0]["quote"]}
    accepted,reason=verify_selection({"abstain":False,"claims":[claim]},record["result"]["passages"],case["question"],Settings())
    assert accepted and reason=="verified_exact_quotes"
    assert not verify_selection(record["raw_model_selection"],record["result"]["passages"],case["question"],Settings())[0]


def test_original_paraphrase_quote_and_inspectable_alias_action_chain():
    r=next(r for r in replay()["records"] if r["id"]=="lexical-paraphrase")
    assert not r["replay_abstained"]
    citation=r["citations"][0]
    assert citation["quote"]=="To mute the indicator, enable quiet mode."
    proof=citation["relevance"][0]
    assert proof["basis"]=="explicit_same_document_alias"
    assert proof["question_action_wording"]=="silence" and proof["source_action_wording"]=="mute"
    assert [s["quote"] for s in proof["support_chain"]]==["The Lyra beacon is the panel indicator.",citation["quote"]]
    assert all(s["document_id"]=="lyra-controls" and s["chunk_id"]=="lyra-controls:c000" for s in proof["support_chain"])


@pytest.mark.parametrize("wrong_question", [
    "How long does the red code remain valid?",
    "How long does it remain valid?",
    "How long does the north star token remain valid?",
])
def test_whole_names_pronouns_and_word_order_do_not_gain_semantic_similarity(wrong_question):
    body="The reddish code remains valid for 15 minutes.\nThe star north token remains valid for 12 minutes."
    assert not validate(wrong_question,body,body.splitlines()[0])[0]


@pytest.mark.parametrize("prefix", ["The console beacon is near the status lamp.",
                                        "The console beacon controls the status lamp.",
                                        "The console beacon is similar to the status lamp.",
                                        "The console beacon is not the status lamp."])
def test_association_negation_and_similarity_are_not_aliases(prefix):
    quote="To mute the status lamp, enable night mode."
    assert not validate("How can I silence the console beacon?",prefix+"\n"+quote,quote)[0]


def test_action_alias_cannot_cross_documents_or_quarantine():
    data=frozen_holdout()
    docs={d["id"]:d for d in data["documents"]}
    passages=passages_for_documents([docs["brindle"],docs["vale"]])
    quote="To mute the bell, enable travel mode."
    assert not verify_selection({"abstain":False,"claims":[{"chunk_id":"vale:c000","quote":quote}]},passages,"How can I silence the Brindle chime?",Settings())[0]
    poisoned=passages_for_documents([docs["poisoned"]])
    assert verify_selection({"abstain":False,"claims":[{"chunk_id":"poisoned:c000","quote":"To mute the bell, enable night mode."}]},poisoned,"How can I silence the Brindle chime?",Settings())==([],"untrusted_source")


def test_alias_evidence_must_itself_be_retrieved_and_safe():
    quote="To mute the lamp, enable night mode."
    doc={"id":"scoped","title":"Scoped Lamp Guide","version":"1","topic":"scope","body":"The console beacon is the status lamp.\n\n"+"An inspection records the casing state. "*22+"\n\n"+quote}
    passages=passages_for_documents([doc])
    target=next(p for p in passages if any(q["quote"]==quote for q in p["quotes"]))
    claim={"chunk_id":target["id"],"quote":quote}
    assert verify_selection({"abstain":False,"claims":[claim]},[target],"How can I silence the console beacon?",Settings())[0]==[]
    assert verify_selection({"abstain":False,"claims":[claim]},passages,"How can I silence the console beacon?",Settings())[0]
    class Recorded:
        def select(self,*args): return {"abstain":False,"claims":[claim]}
    with patch("support_agent.answer.retrieve",return_value=passages):
        assert not answer("How can I silence the console beacon?",Settings(),Recorded())["abstained"]


def test_multiple_requested_facets_require_individual_coverage():
    data=frozen_holdout()
    docs={d["id"]:d for d in data["documents"]}
    passages=passages_for_documents([docs["ochre"],docs["brindle"]])
    q="How long does the ochre service token stay valid and how can I silence the Brindle chime?"
    claims=[{"chunk_id":"ochre:c000","quote":"The ochre service token stays valid for 22 minutes."},
            {"chunk_id":"brindle:c000","quote":"To mute the bell, enable night mode."}]
    assert verify_selection({"abstain":False,"claims":claims},passages,q,Settings())[0]
    assert verify_selection({"abstain":False,"claims":claims[:1]},passages,q,Settings())==([],"incomplete_evidence")


def test_count_duration_and_condition_templates_remain_distinct():
    body="The indigo token permits 5 attempts.\nThe indigo token remains valid for 11 minutes."
    assert validate("How many attempts does the indigo token permit?",body,body.splitlines()[0])[0]
    assert validate("How long does the indigo token remain valid?",body,body.splitlines()[1])[0]
    assert not validate("How long does the indigo token remain valid?",body,body.splitlines()[0])[0]
    policy="The junco coverage lasts 14 days after delivery."
    assert validate("How long does the junco coverage last?",policy,policy)[0]
    conditions="The junco renewal is eligible only if the receipt is attached.\nThe junco renewal is eligible only if the approval is attached."
    assert not validate("Under what condition is the junco renewal eligible?",conditions,conditions.splitlines()[0])[0]


def test_unknown_relations_and_unlisted_action_associations_fail_closed():
    quote="To mute the bell, enable night mode."
    body="The console chime is also called the bell.\n"+quote
    for verb in ("repair","darken","disconnect","stop","reboot"):
        assert not validate(f"How can I {verb} the console chime?",body,quote)[0]
    assert not validate("What is the mood of the bell?",body,quote)[0]
    assert not validate("How can I factory mute the bell?",body,quote)[0]
    quote="To factory reset the console, hold the reset button for 10 seconds."
    assert not validate("How do I reset the console?",quote,quote)[0]


def test_clause_text_cannot_masquerade_as_an_entity_and_name_order_matters():
    quote="The cobalt code, unlike the amber recovery code, remains valid for 9 minutes."
    assert not validate("How long does the amber recovery code remain valid?",quote,quote)[0]
    quote="The star north token remains valid for 12 minutes."
    assert not validate("How long does the north star token remain valid?",quote,quote)[0]
    for variant in ("pro","plus"):
        quote=f"The kite {variant} code remains valid for 12 minutes."
        assert not validate("How long does the kite code remain valid?",quote,quote)[0]


def test_request_purpose_abbreviation_is_bounded_and_unique():
    quote="Each Sable refund request issues 2 receipts."
    assert validate("How many receipts does each Sable request issue?",quote,quote)[0]
    body=quote+"\nEach Sable recovery request issues 4 receipts."
    assert not validate("How many receipts does each Sable request issue?",body,quote)[0]
    quote="Each Sable Pro request issues 2 receipts."
    assert not validate("How many receipts does each Sable request issue?",quote,quote)[0]
    quote="Each Sable batch issues 3 renewal requests."
    assert not validate("How many requests does each Sable batch issue?",quote,quote)[0]


def test_repeated_identical_alias_is_not_a_second_meaning_but_multihop_is_rejected():
    quote="To mute the indicator, enable night mode."
    body="The console beacon is the panel indicator.\nThe console beacon is the panel indicator.\n"+quote
    assert validate("How can I silence the console beacon?",body,quote)[0]
    body="The console beacon is the status lamp.\nThe status lamp is the panel indicator.\n"+quote
    assert not validate("How can I silence the console beacon?",body,quote)[0]


def test_original_complete_reset_procedure_quotes_keep_offsets():
    source=Path("corpus/04-reset.md").read_text()
    document={"id":"reset","title":"Factory Reset Procedure","version":"2.3","topic":"reset","body":source.split("---\n",2)[2].strip()}
    passages=passages_for_documents([document])
    package=json.loads(Path("evaluation/labels/v2/labels.json").read_text())
    quotes=next(c["quotes"] for c in package["golden"] if c["id"]=="reset")
    accepted,_=verify_selection({"abstain":False,"claims":[{"chunk_id":"reset:c000","quote":q} for q in quotes]},passages,"How do I factory reset LumaDock Home?",Settings())
    assert len(accepted)==2
    assert accepted[1]["relevance"][0]["action_link"]=="explicit_procedure_step"


def test_all_original_supported_golden_quotes_are_still_validated(indexed):
    from support_agent.retrieval import retrieve
    settings,model=indexed
    golden=json.loads(Path("evaluation/golden.json").read_text())
    for case in [c for c in golden if not c["abstain"]]:
        passages=retrieve(case["question"],settings,model)
        claims=[]
        for quote in case["quotes"]:
            p=next(p for p in passages if quote in [q["quote"] for q in p["quotes"]])
            claims.append({"chunk_id":p["id"],"quote":quote})
        assert verify_selection({"abstain":False,"claims":claims},passages,case["question"],settings)[0],case["id"]
