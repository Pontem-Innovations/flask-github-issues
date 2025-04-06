import hashlib
import requests
from datetime import datetime
import pytz


class ErrorTracking:
    def __init__(self, app=None):
        self.gh_token = None
        self.gh_repo = None
        self.assignees = []
        self.labels = []
        self.types = []
        if app:
            self.init_app(app)

    # ───────────────────────────── app bootstrap ──────────────────────────────
    def init_app(self, app):
        self.gh_token = app.config.get("GH_TOKEN")
        self.gh_repo = app.config.get("GH_REPO")
        self.assignees = app.config.get("GH_ASSIGNEES", [])
        self.labels = app.config.get("GH_LABELS", [])
        self.types = app.config.get("GH_TYPES", [])
        if not self.gh_token or not self.gh_repo:
            raise ValueError("GH_TOKEN and GH_REPO must be set in configuration.")
        app.extensions["error_tracking"] = self

    # ────────────────────────────── public API ────────────────────────────────
    def track_error(self, *, error_message: str, details: list[dict] | None = None):
        """
        Log an error. `details` is a list of single‑key dicts that will be
        rendered in the issue body, e.g. `[{'User Email': 'a@b.com'}, {'URL': '/foo'}]`.
        """
        if not error_message:
            print("Error message is required.")
            return

        details = details or []
        error_hash = self._hash(error_message)
        timestamp = datetime.now(pytz.timezone("Canada/Mountain")).strftime(
            "%A %B %d %Y %H:%M:%S"
        )

        title = f"{self._strip_error(error_message)} - Key:{error_hash}"
        body = self._build_body(timestamp, error_message, details)

        # duplicate / recurrence detection ------------------------------------
        open_issues = self._get_open_issues()
        for issue in open_issues:
            if error_hash not in issue["title"]:
                continue

            if self._all_detail_values_present(issue, details):
                print("Issue already exists with same details.")
                return

            # look in comments
            comments = self._get_issue_comments(issue["number"])
            if any(self._all_detail_values_present(c, details) for c in comments):
                print("Details already noted in comments.")
                return

            # new occurrence with different metadata
            self._comment_on_issue(
                issue["number"],
                self._build_body(timestamp, "", details, prefix="New occurrence:\n\n"),
            )
            return

        # brand‑new issue
        self._create_issue(title, body)

    # ──────────────────────────── helpers / private ───────────────────────────
    @staticmethod
    def _hash(msg: str) -> str:
        return hashlib.sha1(msg.encode()).hexdigest()

    @staticmethod
    def _strip_error(msg: str) -> str:
        return msg.strip().split("\n")[-1].split(":")[0]

    @staticmethod
    def _build_body(ts: str, err: str, details: list[dict], *, prefix: str = "") -> str:
        md_details = "\n".join(f"**{k}:** {v}" for d in details for k, v in d.items())
        err_block = f"\n**Error Message:**\n```{err}```" if err else ""
        return f"{prefix}**Timestamp:** {ts}\n{md_details}{err_block}"

    @staticmethod
    def _all_detail_values_present(blob: dict, details: list[dict]) -> bool:
        text = blob.get("body", "")
        return all(str(v) in text for d in details for v in d.values())

    # ────────────────────────── GitHub REST helpers ───────────────────────────
    def _get_open_issues(self):
        url = f"https://api.github.com/repos/{self.gh_repo}/issues?state=open"
        return self._gh_get(url)

    def _create_issue(self, title, body):
        url = f"https://api.github.com/repos/{self.gh_repo}/issues"
        data = {
            "title": title,
            "body": body,
            "assignees": self.assignees,
            "labels": self.labels,
            "type": self.types,
        }
        self._gh_post(url, data, "Issue created", "Failed to create issue")

    def _comment_on_issue(self, issue_number, comment):
        url = f"https://api.github.com/repos/{self.gh_repo}/issues/{issue_number}/comments"
        self._gh_post(url, {"body": comment}, "Comment added", "Failed to add comment")

    def _get_issue_comments(self, issue_number):
        url = f"https://api.github.com/repos/{self.gh_repo}/issues/{issue_number}/comments"
        return self._gh_get(url)

    # ────────────────────────────── HTTP wrappers ─────────────────────────────
    def _gh_get(self, url):
        resp = requests.get(url, headers={"Authorization": f"token {self.gh_token}"})
        return resp.json() if resp.status_code == 200 else []

    def _gh_post(self, url, data, ok_msg, err_msg):
        resp = requests.post(url, headers={"Authorization": f"token {self.gh_token}"}, json=data)
        print(ok_msg if resp.status_code in (200, 201) else f"{err_msg}: {resp.status_code}")
