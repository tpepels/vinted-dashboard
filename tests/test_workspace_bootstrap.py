from sqlalchemy import select

from app import db, models
from app.workspace_bootstrap import (
    clean_isbn,
    get_or_create_channel_account,
    get_or_create_owner,
    get_or_create_workspace,
    normalize_sku,
)


def test_clean_isbn_accepts_only_isbn10_or_isbn13():
    assert clean_isbn("978-0-306-40615-7") == "9780306406157"
    assert clean_isbn("0-306-40615-2") == "0306406152"
    assert clean_isbn("not an isbn") is None
    assert clean_isbn(None) is None


def test_normalize_sku_strips_and_blanks_to_none():
    assert normalize_sku("  ABC-1  ") == "ABC-1"
    assert normalize_sku("") is None
    assert normalize_sku(None) is None


def test_workspace_owner_and_channel_helpers_are_idempotent():
    with db.session_scope() as session:
        workspace = get_or_create_workspace(session, "Personal Workspace", "personal")
        same_workspace = get_or_create_workspace(session, "Personal Workspace", "personal")
        assert same_workspace.id == workspace.id

        first_owner = get_or_create_owner(session, workspace, "owner@example.com")
        second_owner = get_or_create_owner(session, workspace, "owner@example.com")
        assert second_owner.id == first_owner.id
        assert len(workspace.memberships) == 1

        account, created = get_or_create_channel_account(session, workspace, "vinted", {})
        same_account, created_again = get_or_create_channel_account(
            session, workspace, "vinted", {}
        )
        assert created is True
        assert created_again is False
        assert same_account.id == account.id

    with db.session_scope() as session:
        assert session.execute(
            select(models.Workspace).where(models.Workspace.slug == "personal")
        ).scalar_one().id == workspace.id
