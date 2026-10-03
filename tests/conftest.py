import io
import itertools

import pytest
from django.core.management import call_command

from accounts.models import User
from boards.models import SubForum
from tests.factories import grant

_counter = itertools.count(1)


@pytest.fixture
def seeded(db, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.test")
    monkeypatch.setenv("OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setenv("OWNER_DISPLAY_NAME", "Test Owner")
    call_command("seed", stdout=io.StringIO())


@pytest.fixture
def owner(seeded):
    return User.objects.get(email="owner@example.test")


@pytest.fixture
def subforums(seeded):
    return {sf.slug: sf for sf in SubForum.objects.all()}


@pytest.fixture
def lobby(subforums):
    return subforums["guest-lobby"]


@pytest.fixture
def general(subforums):
    return subforums["general-discussion"]


@pytest.fixture
def serious(subforums):
    return subforums["serious-discussion"]


@pytest.fixture
def seminars(subforums):
    return subforums["seminars"]


@pytest.fixture
def make_user(seeded):
    """make_user("full") creates a member whose trust role is `full`. Extra roles (e.g. a scoped
    Moderator assignment) are added with grant()."""

    def make(role=None, status=None, granted_at=None, **fields):
        n = next(_counter)
        if status is None:
            status = User.Status.GUEST if role == "guest" else User.Status.ACTIVE
        user = User.objects.create_user(
            email=f"member{n}@example.test",
            password="not-a-real-password",
            display_name=f"Member {n}",
            slug=f"member-{n}",
            status=status,
            **fields,
        )
        if role:
            grant(user, role, granted_at=granted_at)
        return user

    return make
