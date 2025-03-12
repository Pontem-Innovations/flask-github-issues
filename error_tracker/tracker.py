import hashlib
import requests
from datetime import datetime
import pytz
import os
from dotenv import load_dotenv

class ErrorTracking:
    def __init__(self, client_name=None, gh_token=None, gh_repo=None, assignees=None, labels=None):
        """Initialize the error tracking class."""
        base_dir = os.path.abspath(os.path.dirname(__file__))
        load_dotenv(os.path.join(base_dir, ".env"))

        self.client_name = client_name or os.getenv("CLIENT_NAME")
        self.gh_token = gh_token or os.getenv("gh_token")
        self.gh_repo = gh_repo or os.getenv("GH_REPO")
        self.assignees = assignees or os.getenv("GH_ASSIGNEES", "").split(",") if os.getenv("GH_ASSIGNEES") else []
        self.labels = labels or os.getenv("GH_LABELS", "").split(",") if os.getenv("GH_LABELS") else ["bug"]

        if not self.gh_token or not self.gh_repo:
            raise ValueError("GitHub token and repository must be provided.")

    def hash_error(self, error_message):
        return hashlib.sha1(error_message.encode()).hexdigest()

    def track_error(self, error_message=None, user_email=None, url=None):
        """Track an error and create an issue in GitHub if necessary."""
        if not error_message:
            print("Error message is required.")
            return

        error_hash = self.hash_error(error_message)
        timestamp = datetime.now(pytz.timezone("Canada/Mountain")).strftime("%A %B %d %Y %H:%M:%S")
        error_message_strip = error_message.strip().split("\n")[-1].split(":")[0]
        title = f"{error_message_strip} - Key:{error_hash}"
        body = f"**Timestamp:** {timestamp}\n**User Email:** {user_email if user_email else ''}\n**URL:** {url if url else ''}\n**Error Message:**\n```{error_message}```"

        open_issues = self.get_open_issues()
        for issue in open_issues:
            if error_hash in issue["title"]:
                if user_email in issue["body"]:
                    print("Issue already exists.")
                    return
                comments = self.get_issue_comments(issue["number"])
                if any(user_email in comment["body"] for comment in comments):
                    print("User has already reported this issue.")
                    return
                self.comment_on_issue(issue["number"], f"New occurrence detected:\n\n**Timestamp:** {timestamp}\n**User Email:** {user_email}\n**URL:** {url}")
                return

        self.create_issue(title, body)

    def get_open_issues(self):
        """Retrieve open issues from GitHub."""
        url = f"https://api.github.com/repos/{self.gh_repo}/issues?state=open"
        headers = {"Authorization": f"token {self.gh_token}"}
        response = requests.get(url, headers=headers)
        return response.json() if response.status_code == 200 else []

    def create_issue(self, title, body):
        """Create a new issue on GitHub."""
        url = f"https://api.github.com/repos/{self.gh_repo}/issues"
        headers = {"Authorization": f"token {self.gh_token}"}
        data = {"title": title, "body": body, "assignees": self.assignees, "labels": self.labels}
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 201:
            print("Issue created successfully.")
        else:
            print(f"Failed to create issue: {response.status_code}")

    def comment_on_issue(self, issue_number, comment):
        """Add a comment to an existing GitHub issue."""
        url = f"https://api.github.com/repos/{self.gh_repo}/issues/{issue_number}/comments"
        headers = {"Authorization": f"token {self.gh_token}"}
        data = {"body": comment}
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 201:
            print("Comment added successfully.")
        else:
            print(f"Failed to add comment: {response.status_code}")

    def get_issue_comments(self, issue_number):
        """Retrieve comments from a GitHub issue."""
        url = f"https://api.github.com/repos/{self.gh_repo}/issues/{issue_number}/comments"
        headers = {"Authorization": f"token {self.gh_token}"}
        response = requests.get(url, headers=headers)
        return response.json() if response.status_code == 200 else []