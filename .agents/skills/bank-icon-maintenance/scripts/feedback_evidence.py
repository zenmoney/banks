"""Exact reviewer attachment -> final render comparison, in addition to vector fidelity.

The attachment may be raster research. It never replaces original-vector checks,
actual renderer evidence, or the agent's explicit same-variant visual judgment.
"""
import copy
from datetime import datetime, timezone

from evidence import instant, required
import feedback_revision
import source_evidence as source



def context(session, data, bank):
    if bank.get('taskContext'):
        from task_work import attachment_context
        return attachment_context(session,data,bank)
    return feedback_revision.context(session,data,bank)


def required_for(session, data, bank):
    if bank.get('taskContext'):
        return context(session,data,bank) is not None
    return bool(bank.get('feedbackContext'))


def raw_reference(session, data, bank, receipt_link, checked):
    ctx = context(session, data, bank)
    if ctx is None:
        raise ValueError("reviewer reference requires a queued feedback context")
    url = ctx["request"]["selectedUrl"]
    receipt, body = source.retrieval(session, receipt_link, bank["id"], data["baseCommit"], checked, url)
    if receipt["requestedUrl"] != url:
        raise ValueError("reviewer reference must start at the exact selected attachment URL")
    if instant(receipt["startedAt"], "retrieval.startedAt") < instant(ctx["request"]["recordedAt"], "feedback.recordedAt"):
        raise ValueError("reviewer attachment was not fetched for this sourced request")
    return ctx, {"path": receipt["body"]["path"], "iconHash": receipt["body"]["sha256"],
                 "sourceAssetUrl": url, "sourceUrl": ctx["request"]["event"]["source"]["url"],
                 "retrieval": receipt_link}


def view(session, data, bank, receipt_link, final, bundle_link):
    ctx, raw = raw_reference(session, data, bank, receipt_link, datetime.now(timezone.utc))
    reference = source.prepare_reference(session, raw, data, bank)
    comparison = source.prepare_comparison(session, raw, data, bank, final, bundle_link, reference)
    value = source.read_json(source.linked(session, comparison, "reviewer comparison"))
    evidence = {"requestKey": ctx["requestKey"], "retrieval": receipt_link,
                "sourceSha256": raw["iconHash"], "comparison": comparison}
    root = source.fresh(session, bank["id"], "reviewer-view")
    source.write_json(root / "view.json", evidence)
    return {"view": source.link(session, root / "view.json"), "pairs": value["pairs"],
            "nextAction": "Inspect the full requested attachment versus this exact final icon on both themes. A similar logo or successful vector conversion does not prove the selected variant."}


def assemble(session, data, bank, view_link, judgement):
    if not required_for(session, data, bank):
        raise ValueError("reviewer evidence belongs only to a sourced feedback revision")
    value = source.read_json(source.linked(session, view_link, "reviewer view"))
    if value.keys() != {"requestKey", "retrieval", "sourceSha256", "comparison"}:
        raise ValueError("reviewer view has unsupported fields")
    return {**value, "judgement": copy.deepcopy(judgement)}


def validate(session, data, bank, payload):
    value = payload.get("reviewerFeedback")
    if not required_for(session, data, bank):
        if value is not None:
            raise ValueError("unsolicited reviewer evidence cannot create revision eligibility")
        return
    if not isinstance(value, dict) or value.keys() != {"requestKey", "retrieval", "sourceSha256", "comparison", "judgement"}:
        raise ValueError("feedback candidate requires complete reviewer-attachment comparison")
    checked = instant(payload["checkedAt"], "candidate.checkedAt")
    ctx, raw = raw_reference(session, data, bank, value["retrieval"], checked)
    if value["requestKey"] != ctx["requestKey"] or value["sourceSha256"] != raw["iconHash"]:
        raise ValueError("reviewer comparison binds a different request or attachment bytes")
    judgement = value["judgement"]
    if not isinstance(judgement, dict) or judgement.keys() != {"sameVariant", "statement"} or judgement["sameVariant"] is not True:
        raise ValueError("reviewer-selected variant has not been confirmed by an actual visual judgment")
    required(judgement, "statement")
    view = source.read_json(source.linked(session, value["comparison"], "reviewer comparison"))
    reference = source.read_json(source.linked(session, view["sourceReference"], "reviewer reference"))
    # Do not alter the vector-source comparison: validate an independent set of
    # source halves against the same actual final/Desktop bytes and timestamps.
    compared = {**payload, "comparison": {
        theme: {**payload["comparison"][theme], "source": reference["images"][theme]}
        for theme in source.BACKGROUNDS}}
    if reference.get("crop") is not None or "sourceMaterial" in reference:
        raise ValueError("reviewer comparison must retain the entire selected attachment")
    source.validate_comparison(session, value["comparison"], raw, compared, data, bank, checked)


def links(session, payload):
    value = payload.get("reviewerFeedback")
    if value is None:
        return []
    view = source.read_json(source.linked(session, value["comparison"], "reviewer comparison"))
    reference = source.read_json(source.linked(session, view["sourceReference"], "reviewer reference"))
    result = [value["retrieval"], value["comparison"], view["sourceReference"], view["desktopBundle"],
              view["final"], reference["source"], reference["raster"], *view["pairs"].values(),
              *reference["images"].values()]
    if "log" in reference["invocation"]:
        result.append(reference["invocation"]["log"])
    return result


def record(proposal, payload):
    if "reviewerFeedback" in payload:
        proposal["reviewerFeedback"] = copy.deepcopy(payload["reviewerFeedback"])
