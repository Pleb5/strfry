"""Ordered write-policy pipeline shared by all stages."""

from dataclasses import dataclass, field


ACCEPT = "accept"
REJECT = "reject"
SHADOW_REJECT = "shadowReject"


@dataclass
class Decision:
    action: str
    msg: str = ""
    reason: str = ""

    @classmethod
    def accept(cls):
        return cls(ACCEPT)

    @classmethod
    def reject(cls, msg, reason=""):
        return cls(REJECT, msg, reason)

    @classmethod
    def shadow_reject(cls, reason=""):
        return cls(SHADOW_REJECT, "", reason)

    @property
    def accepted(self):
        return self.action == ACCEPT


@dataclass
class Request:
    """A parsed strfry plugin request."""

    raw: dict
    event: dict
    event_id: str
    pubkey: str
    kind: int
    tags: list
    source_type: str
    source_info: str
    received_at: int
    authed: str = ""
    annotations: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, raw):
        event = raw.get("event") or {}
        if not isinstance(event, dict):
            event = {}
        tags = event.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        tags = [
            [str(item) for item in tag] if isinstance(tag, list) else []
            for tag in tags
        ]
        event = dict(event)
        event["tags"] = tags
        kind = event.get("kind")
        try:
            kind = int(kind)
        except (TypeError, ValueError):
            kind = -1
        event["kind"] = kind
        received_at = raw.get("receivedAt")
        try:
            received_at = int(received_at)
        except (TypeError, ValueError):
            received_at = 0
        try:
            event["created_at"] = int(event.get("created_at") or 0)
        except (TypeError, ValueError):
            event["created_at"] = 0
        return cls(
            raw=raw,
            event=event,
            event_id=str(event.get("id") or ""),
            pubkey=str(event.get("pubkey") or ""),
            kind=kind,
            tags=tags,
            source_type=str(raw.get("sourceType") or "unknown"),
            source_info=str(raw.get("sourceInfo") or "unknown"),
            received_at=received_at,
            authed=str(raw.get("authed") or ""),
        )

    @property
    def source(self):
        return f"{self.source_type}:{self.source_info}"


class Stage:
    """Base class for pipeline stages."""

    name = "stage"

    def evaluate(self, request, now):
        """Return a Decision, or None to accept without opinion."""
        return None

    def commit(self, request, now):
        """Called after the whole pipeline accepted the request."""

    def health(self):
        """Return (ok, message) for health checks."""
        return True, ""


class Pipeline:
    def __init__(self, stages, clock, on_decision=None):
        self.stages = list(stages)
        self.clock = clock
        self.on_decision = on_decision

    def decide(self, raw):
        request = Request.parse(raw)
        now = self.clock()

        if raw.get("type") != "new" or not request.event_id:
            decision = Decision.reject(
                "blocked: invalid policy request", "invalid_request"
            )
            return request, decision, None

        for stage in self.stages:
            decision = stage.evaluate(request, now)
            if decision is not None and not decision.accepted:
                return request, decision, stage.name

        for stage in self.stages:
            stage.commit(request, now)

        return request, Decision.accept(), None

    def handle(self, raw):
        request, decision, stage_name = self.decide(raw)
        if self.on_decision is not None:
            self.on_decision(request, decision, stage_name)
        response = {"id": request.event_id, "action": decision.action}
        if decision.accepted and request.annotations.get("policyRelevant") is True:
            response["policyRelevant"] = True
        if decision.action == REJECT and decision.msg:
            response["msg"] = decision.msg
        return response

    def health(self):
        problems = []
        for stage in self.stages:
            ok, message = stage.health()
            if not ok:
                problems.append(f"{stage.name}: {message}")
        return (not problems), problems
