"""A team whose thinking roles and doing roles come from different implementations."""

from controller.app.team.base import CallRecord


class CompositeTeam:
    def __init__(self, thinking, doing):
        self.thinking, self.doing = thinking, doing

    @property
    def engine(self) -> str:
        return self.thinking.engine

    @property
    def last_call(self) -> CallRecord | None:
        return self.thinking.last_call

    def draft_intent(self, *args, **kwargs):
        return self.thinking.draft_intent(*args, **kwargs)

    def specify(self, *args, **kwargs):
        return self.thinking.specify(*args, **kwargs)

    def write_srs(self, *args, **kwargs):
        return self.thinking.write_srs(*args, **kwargs)

    def write_sds(self, *args, **kwargs):
        return self.thinking.write_sds(*args, **kwargs)

    def plan(self, *args, **kwargs):
        return self.thinking.plan(*args, **kwargs)

    def design_tests(self, *args, **kwargs):
        return self.thinking.design_tests(*args, **kwargs)

    def review(self, *args, **kwargs):
        return self.thinking.review(*args, **kwargs)

    def extract_part(self, *args, **kwargs):
        return self.thinking.extract_part(*args, **kwargs)

    def consolidate(self, *args, **kwargs):
        return self.thinking.consolidate(*args, **kwargs)

    def read_minutes(self, *args, **kwargs):
        return self.thinking.read_minutes(*args, **kwargs)

    def write_tests(self, *args, **kwargs):
        return self.doing.write_tests(*args, **kwargs)

    def red_check(self, *args, **kwargs):
        return self.doing.red_check(*args, **kwargs)

    def implement(self, *args, **kwargs):
        return self.doing.implement(*args, **kwargs)

    def validate(self, *args, **kwargs):
        return self.doing.validate(*args, **kwargs)

    def open_pull_request(self, *args, **kwargs):
        opener = getattr(self.doing, "open_pull_request", None)
        return opener(*args, **kwargs) if opener else self.thinking.open_pull_request(*args, **kwargs)
