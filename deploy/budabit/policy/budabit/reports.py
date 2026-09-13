"""NIP-56 censor overlay: effective person bans and censored addresses.

Port of ``getEffectiveCommunityReportState`` in Budabit's
``src/app/core/community-reports.ts``. Only the parts that influence write
permission are needed on the relay: the effective person-ban set (which
removes write access) and the effective event-report set (optional
censored-address rejection).
"""

from dataclasses import dataclass, field

from . import protocol


@dataclass
class ReportState:
    person_bans: set = field(default_factory=set)
    censored_addresses: set = field(default_factory=set)
    censored_event_ids: set = field(default_factory=set)
    person_report_ids: set = field(default_factory=set)
    event_report_ids: set = field(default_factory=set)


class AuthorityView:
    """The subset of branch state the report rules need.

    ``definition``            parsed Definition
    ``section_moderators(s)`` owner plus owners of current non-declined shards
                              referenced by section ``s`` (no ban filtering)
    """

    def __init__(self, definition, section_moderators):
        self.definition = definition
        self._section_moderators = section_moderators

    @property
    def owner(self):
        return self.definition.owner

    def section_moderators(self, section):
        return self._section_moderators(section)

    def current_moderators(self):
        result = set()
        for section in self.definition.sections:
            result |= self.section_moderators(section)
        return result

    def all_sections_moderators(self):
        sections = self.definition.sections
        if not sections:
            return set()
        result = set(self.section_moderators(sections[0]))
        for section in sections[1:]:
            result &= self.section_moderators(section)
        return result


def _is_deleted(report, deletes_for_id):
    return any(protocol.is_report_delete(delete, report) for delete in deletes_for_id)


def compute_report_state(view, report_events, deletes_by_report_id, target_author_lookup=None):
    """Return ReportState for a branch.

    ``report_events``          iterable of stored kind:1984 events
    ``deletes_by_report_id``   mapping report id -> list of kind:5 events
    """
    definition = view.definition
    owner = view.owner
    current_moderators = view.current_moderators()
    all_sections_moderators = view.all_sections_moderators()

    parsed = []
    for event in report_events:
        report = protocol.parse_report(event, target_author_lookup)
        if not report:
            continue
        if report.authority.address != definition.address:
            continue
        if _is_deleted(report, deletes_by_report_id.get(report.id, ())):
            continue
        reporter = protocol.normalize_pubkey(report.reporter or "")
        admin_authored = reporter == owner
        # Moderators cannot moderate the admin or another current moderator.
        if not admin_authored and (
            report.target_pubkey == owner or report.target_pubkey in current_moderators
        ):
            continue
        parsed.append((report, reporter, admin_authored))

    def authorized_person_report(report, reporter, admin_authored):
        if not reporter or reporter == report.target_pubkey:
            return False
        if admin_authored:
            return True
        return reporter in all_sections_moderators

    def authorized_event_report(report, reporter, admin_authored):
        if not reporter or reporter == report.target_pubkey or not report.section_name:
            return False
        section = definition.section_named(report.section_name)
        if section is None:
            return False
        if admin_authored:
            return True
        return reporter in view.section_moderators(section)

    # Fixpoint: a person report by a reporter who is themselves effectively
    # banned stops counting, which may un-ban others, and so on.
    person_reports = []
    banned = set()
    for _ in range(len(parsed) + 1):
        next_reports = [
            item
            for item in parsed
            if item[0].target == "person"
            and item[1] not in banned
            and authorized_person_report(*item)
        ]
        next_ids = sorted(item[0].id for item in next_reports)
        current_ids = sorted(item[0].id for item in person_reports)
        person_reports = next_reports
        banned = {item[0].target_pubkey for item in person_reports}
        if next_ids == current_ids:
            break

    event_reports = [
        item
        for item in parsed
        if item[0].target == "event" and item[1] not in banned and authorized_event_report(*item)
    ]

    state = ReportState()
    state.person_bans = banned
    state.person_report_ids = {item[0].id for item in person_reports}
    state.event_report_ids = {item[0].id for item in event_reports}
    for report, _, _ in event_reports:
        if report.target_address:
            state.censored_addresses.add(report.target_address)
        if report.target_event_id:
            state.censored_event_ids.add(report.target_event_id)
    return state
