import pytest
from flask import Flask
import requests_mock
from flask_github_issues import ErrorTracking


@pytest.fixture
def tracker():
    """Create a Flask app and initialise ErrorTracking with test config."""
    app = Flask(__name__)
    app.config.update(
        GH_TOKEN="fake_token",
        GH_REPO="testorg/testrepo",
        GH_ASSIGNEES=["testuser"],
        GH_LABELS=["bug"],
        GH_TYPES=["issue"],
    )
    et = ErrorTracking()
    et.init_app(app)
    return et


# ───────────────────────── hash helper ──────────────────────────
def test_hash_error(tracker):
    error_message = "This is a test error"
    error_hash = tracker._hash(error_message)       # new name
    assert len(error_hash) == 40
    assert isinstance(error_hash, str)


# ───────────────────────── open issues ──────────────────────────
def test_get_open_issues(tracker):
    with requests_mock.Mocker() as m:
        m.get(
            "https://api.github.com/repos/testorg/testrepo/issues?state=open",
            json=[{"title": "Test Issue", "number": 1}],
            status_code=200,
        )
        issues = tracker._get_open_issues()         # new name
        assert len(issues) == 1
        assert issues[0]["title"] == "Test Issue"


# ───────────────────────── create issue ─────────────────────────
def test_create_issue(tracker):
    with requests_mock.Mocker() as m:
        m.post(
            "https://api.github.com/repos/testorg/testrepo/issues",
            json={"number": 1, "title": "Test Issue"},
            status_code=201,
        )
        tracker._create_issue("Test Issue", "Test Body")   # new name


# ───────────────────────── comment issue ────────────────────────
def test_comment_on_issue(tracker):
    with requests_mock.Mocker() as m:
        m.post(
            "https://api.github.com/repos/testorg/testrepo/issues/1/comments",
            json={"id": 1, "body": "Test Comment"},
            status_code=201,
        )
        tracker._comment_on_issue(1, "Test Comment")       # new name


# ───────────────────────── get comments ────────────────────────
def test_get_issue_comments(tracker):
    with requests_mock.Mocker() as m:
        m.get(
            "https://api.github.com/repos/testorg/testrepo/issues/1/comments",
            json=[{"id": 1, "body": "Existing comment"}],
            status_code=200,
        )
        comments = tracker._get_issue_comments(1)          # new name
        assert len(comments) == 1
        assert comments[0]["body"] == "Existing comment"
