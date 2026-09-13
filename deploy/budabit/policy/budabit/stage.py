"""Pipeline stage: Budabit community write control."""

from ..pipeline import Decision, Stage
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
                self.state, scanner, self.metrics, reconcile_seconds=config.reconcile_seconds
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
        for branch, change in self.state.apply(request.event, inline=True):
            self.metrics.state_change(branch, change, request.event)

    def health(self):
        if not self.config.enabled:
            return True, ""
        problems = []
        for branch in self.state.branches.values():
            if not branch.warm:
                problems.append(f"{branch.community_id[:8]} not warm")
            elif not branch.derived().available:
                problems.append(f"{branch.community_id[:8]} has no valid definition")
        if self.loader is not None and self.loader.last_error:
            problems.append(f"loader: {self.loader.last_error[:120]}")
        return (not problems), "; ".join(problems)

    def status(self):
        rows = []
        for branch in self.state.branches.values():
            derived = branch.derived()
            rows.append(
                {
                    "address": branch.address,
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
