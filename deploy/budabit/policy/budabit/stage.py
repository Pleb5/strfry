"""Pipeline stage: Budabit community write control."""

from ..pipeline import Decision, Stage
from . import protocol as P
from . import rules
from .config import BudabitConfig
from .loader import Loader, StrfryScanner
from .metrics import Metrics
from .state import CommunityState


class BudabitWriteControl(Stage):
    name = "budabit"

    def __init__(self, config, metrics=None, scanner=None, start_loader=True):
        self.config = config
        self.metrics = metrics or Metrics()
        self.state = CommunityState(config.branches)
        self.loader = None
        if config.enabled and config.loader_enabled:
            scanner = scanner or StrfryScanner(config)
            self.loader = Loader(
                self.state,
                scanner,
                self.metrics,
                reconcile_seconds=config.reconcile_seconds,
                auto_host_url=config.auto_host_url,
            )
            if start_loader:
                self.loader.start()
        elif not config.loader_enabled:
            # Tests and offline replay: every branch is considered warm.
            for branch in self.state.branches.values():
                branch.warm = True

    @classmethod
    def from_env(cls, env, **kwargs):
        return cls(BudabitConfig.from_env(env), **kwargs)

    def evaluate(self, request, now):
        if not self.config.enabled:
            return None
        if self.loader is not None and not self.loader.initialized:
            # Strfry starts/restarts a persistent plugin on demand. Its first
            # event must not race background discovery and pass as "unhosted".
            # Gate all writes until this instance has a complete initial view;
            # even dry-run cannot waive readiness. Keep known protected-delete
            # denials permanent rather than advising a pointless retry.
            outcome = None
            if request.kind == P.DELETE_KIND:
                outcome = rules.evaluate(request.event, self.state, self.config)
            if outcome is None or outcome.accepted:
                outcome = rules.Outcome(
                    Decision.reject(rules.LOADING_MESSAGE),
                    "warming_up",
                    scope="initializing",
                )
            request.annotations["budabit"] = outcome
            self.metrics.decision(request, outcome)
            return outcome.decision
        outcome = rules.evaluate(request.event, self.state, self.config)
        request.annotations["budabit"] = outcome
        self.metrics.decision(request, outcome, dry_run=self.config.dry_run)
        if outcome.accepted:
            return None
        if self.config.dry_run:
            return None
        return outcome.decision

    def commit(self, request, now):
        if not self.config.enabled:
            return
        outcome = request.annotations.get("budabit")
        if outcome is None or not outcome.authority:
            return
        if outcome.reason == "auto_host":
            address = P.get_addressable_address(request.event)
            branch = self.state.add_branch(address, auto=True)
            if branch is not None:
                self.metrics.log("branch_auto_hosted", community=branch.community_id[:8], address=address)
        for branch, change in self.state.apply(request.event, inline=True):
            self.metrics.state_change(branch, change, request.event)

    def nip11_extra(self):
        """Suggested `relay.info.extra` JSON advertising this relay's enforcement."""
        payload = {
            "policy_version": self.config.policy_version,
            "mode": "strict" if self.config.strict else "passthrough",
            "dry_run": self.config.dry_run,
            "enforcing": self.config.enabled and not self.config.dry_run,
            "configured_branches": sorted(self.state.branches),
            "enforced_branches": [] if self.config.dry_run else sorted(self.state.branches),
            "protected_deletion_kinds": sorted(rules.PROTECTED_DELETE_KINDS),
        }
        if self.config.auto_host_url:
            payload["auto_host"] = self.config.auto_host_url
        return {"budabit": payload}

    def health(self):
        if not self.config.enabled:
            return True, ""
        problems = []
        if self.loader is not None and not self.loader.initialized:
            problems.append("initial authority load not complete")
        for branch in list(self.state.branches.values()):
            if not branch.warm:
                problems.append(f"{branch.community_id[:8]} not warm")
            elif not branch.derived().available and not self.state.is_auto(branch.address):
                problems.append(f"{branch.community_id[:8]} has no valid definition")
        if self.loader is not None and self.loader.last_error:
            problems.append(f"loader: {self.loader.last_error[:120]}")
        return (not problems), "; ".join(problems)

    def status(self):
        rows = []
        for branch in list(self.state.branches.values()):
            derived = branch.derived()
            rows.append(
                {
                    "address": branch.address,
                    "auto": self.state.is_auto(branch.address),
                    "warm": branch.warm,
                    "definition": derived.available,
                    "sections": [s.name for s in derived.definition.sections] if derived.available else [],
                    "shards": {
                        address: bool(coord.current) for address, coord in branch.shards.items()
                    },
                    "grants": {k: len(v) for k, v in derived.section_grants.items()},
                    "structural_members": len(derived.structural_members),
                    "moderators": len(derived.current_moderators),
                    "person_bans": len(derived.person_bans),
                    "reports": len(branch.reports),
                }
            )
        return rows
