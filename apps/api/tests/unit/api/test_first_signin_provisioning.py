"""_get_or_create_user: concurrent first sign-in and email collision paths."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from sourcemind.core import dependencies
from sourcemind.core.exceptions import ValidationError


def _row(clerk_id: str = "user_x", email: str = "new@example.com"):
    return SimpleNamespace(
        id=uuid.uuid4(), clerk_id=clerk_id, email=email, display_name="New"
    )


def _result(row):
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    return result


def _db(selects: list, *, insert_error: Exception | None = None):
    db = MagicMock()
    # execute(): the selects in order, with the insert between first and second.
    queue = [_result(selects[0]), insert_error or _result(None), *map(_result, selects[1:])]

    async def execute(_stmt):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    db.execute = AsyncMock(side_effect=execute)
    savepoint = MagicMock()
    savepoint.__aenter__ = AsyncMock(return_value=None)
    savepoint.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=savepoint)
    return db


def _integrity_error() -> IntegrityError:
    return IntegrityError("INSERT", {}, Exception("duplicate key"))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_existing_user_is_returned_without_insert() -> None:
    row = _row()
    db = _db([row])
    user = await dependencies._get_or_create_user(db, "user_x", row.email, "New")
    assert user.user_id == row.id
    assert db.execute.await_count == 1
    db.begin_nested.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_first_signin_inserts_then_reselects_the_row() -> None:
    row = _row()
    db = _db([None, row])
    user = await dependencies._get_or_create_user(db, "user_x", row.email, "New")
    assert user.user_id == row.id
    assert db.execute.await_count == 3  # select, insert, select


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lost_clerk_id_race_reselects_the_winners_row() -> None:
    row = _row()
    db = _db([None, row], insert_error=_integrity_error())
    user = await dependencies._get_or_create_user(db, "user_x", row.email, "New")
    assert user.user_id == row.id
    assert user.clerk_id == "user_x"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_email_belonging_to_another_identity_is_a_handled_409() -> None:
    db = _db([None, None], insert_error=_integrity_error())
    with pytest.raises(ValidationError) as caught:
        await dependencies._get_or_create_user(db, "user_new", "taken@example.com", None)
    assert caught.value.http_status == 409
    assert caught.value.details == {"reason": "email_already_linked"}
    assert "taken@example.com" not in caught.value.message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_silent_do_nothing_with_no_row_is_also_the_email_conflict() -> None:
    # ON CONFLICT (clerk_id) never fires for an email clash, but if the insert
    # reports nothing and no row exists the outcome must still be the 409.
    db = _db([None, None])
    with pytest.raises(ValidationError) as caught:
        await dependencies._get_or_create_user(db, "user_new", "taken@example.com", None)
    assert caught.value.http_status == 409
