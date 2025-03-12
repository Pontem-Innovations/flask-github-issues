import pytest
from flask import Flask
import requests_mock
from flask_github_issues import ErrorTracking


@pytest.fixture
def tracker():
    """Creates a Flask app and initializes ErrorTracking with test configuration."""
    app = Flask(__name__)
    app.config["GH_TOKEN"] = "fake_token"
    app.config["GH_REPO"] = "testorg/testrepo"
    app.config["GH_ASSIGNEES"] = ["testuser"]
    app.config["GH_LABELS"] = ["bug"]
    app.config["GH_TYPES"] = ["issue"]  # Add this to match your class update

    tracker = ErrorTracking()
    tracker.init_app(app)  # Manually initialize the extension
    return tracker

def test_hash_error(tracker):
    error_message = "This is a test error"
    error_hash = tracker.hash_error(error_message)
    assert len(error_hash) == 40  # SHA1 hashes are 40 characters long
    assert isinstance(error_hash, str)


def test_get_open_issues(tracker):
    with requests_mock.Mocker() as m:
        m.get(
            "https://api.github.com/repos/testorg/testrepo/issues?state=open",
            json=[{"title": "Test Issue", "number": 1}],
            status_code=200,
        )
        issues = tracker.get_open_issues()
        assert len(issues) == 1
        assert issues[0]["title"] == "Test Issue"


def test_create_issue(tracker):
    with requests_mock.Mocker() as m:
        m.post(
            "https://api.github.com/repos/testorg/testrepo/issues",
            json={"number": 1, "title": "Test Issue"},
            status_code=201,
        )
        tracker.create_issue("Test Issue", "Test Body")


def test_comment_on_issue(tracker):
    with requests_mock.Mocker() as m:
        m.post(
            "https://api.github.com/repos/testorg/testrepo/issues/1/comments",
            json={"id": 1, "body": "Test Comment"},
            status_code=201,
        )
        tracker.comment_on_issue(1, "Test Comment")


def test_get_issue_comments(tracker):
    with requests_mock.Mocker() as m:
        m.get(
            "https://api.github.com/repos/testorg/testrepo/issues/1/comments",
            json=[{"id": 1, "body": "Existing comment"}],
            status_code=200,
        )
        comments = tracker.get_issue_comments(1)
        assert len(comments) == 1
        assert comments[0]["body"] == "Existing comment"
