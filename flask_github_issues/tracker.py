import hashlib
import requests
from datetime import datetime
import pytz
import re
from urllib.parse import urlparse
import ipaddress
import sys

class ErrorTracking:
    def __init__(self, app=None):
        self.gh_token = None
        self.gh_repo = None
        self.assignees = []
        self.labels = []
        self.types = []
        self.include_localhost = False
        self.reraise_local = True
        self.project_cfg = None
        if app:
            self.init_app(app)

    # ───────────────────────────── app bootstrap ──────────────────────────────
    def init_app(self, app):
        self.gh_token = app.config.get("GH_TOKEN")
        self.gh_repo = app.config.get("GH_REPO")
        self.assignees = app.config.get("GH_ASSIGNEES", [])
        self.labels = app.config.get("GH_LABELS", [])
        self.types = app.config.get("GH_TYPES", [])

        self.include_localhost = app.config.get("GH_INCLUDE_LOCALHOST", False)
        self.reraise_local = app.config.get("GH_RERAISE_LOCAL_ON_SKIP", True)
        self.project_cfg = app.config.get("GH_PROJECT")

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

        if not self.include_localhost and self._details_contain_local(details):
            print("Skipping error because URL is localhost/private and GH_INCLUDE_LOCALHOST is False.")
            #reraise the error if you want to see it in the logs
            if self.reraise_local:
                et, ev, tb = sys.exc_info()
                if ev is not None:                 # only works when called inside an except:
                    raise ev.with_traceback(tb)    # re-raise original with original traceback
            return

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

        # brand-new issue
        created = self._create_issue(title, body)
        if not created:
            return

        if self.project_cfg:
            try:
                self._project_add_and_update(created)
            except Exception as e:
                print(f"Project update failed: {e}")

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
    

    def _details_contain_local(self, details: list[dict]) -> bool:
        # look for a value that looks like a URL in any detail key/value
        candidates = []
        for d in details:
            for v in d.values():
                if isinstance(v, str):
                    candidates.append(v)
        # crude URL pick-up
        urls = [m.group(0) for c in candidates for m in re.finditer(r"https?://[^\s)]+", c)]
        # also treat a bare path like "URL: /foo" as not local—only real hosts are checked
        for u in urls:
            try:
                host = urlparse(u).hostname or ""
                if host in {"localhost", "127.0.0.1", "::1"}:
                    return True
                # private ranges
                try:
                    ip = ipaddress.ip_address(host)
                    if ip.is_private or ip.is_loopback or ip.is_link_local:
                        return True
                except ValueError:
                    # not an IP -> maybe name like "dev.local" (treat as local if endswith .local)
                    if host.endswith(".local"):
                        return True
            except Exception:
                pass
        return False

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
        resp = requests.post(url, headers=self._rest_headers(), json=data)
        if resp.status_code in (200, 201):
            print("Issue created")
            return resp.json()            # <-- return the issue payload (has node_id)
        print(f"Failed to create issue: {resp.status_code} {resp.text}")
        return None

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


    def _rest_headers(self):
        return {"Authorization": f"token {self.gh_token}", "Accept": "application/vnd.github+json"}

    # ───────────────────────── Projects v2 (GraphQL) ──────────────────────────
    def _project_add_and_update(self, created_issue: dict):
        """
        Add newly created issue to a Project (v2) and set field values.
        """
        cfg = self.project_cfg or {}
        owner = cfg.get("owner")
        if not owner:
            raise ValueError("GH_PROJECT.owner is required")

        project_number = cfg.get("project_number")
        project_title = cfg.get("project_title")

        # Resolve project id + fields
        proj = self._get_project(owner, project_number, project_title)
        project_id = proj["id"]
        fields = {f["name"]: f for f in proj["fields"]}

        # Add the issue to the project
        issue_node_id = created_issue.get("node_id")
        if not issue_node_id:
            raise RuntimeError("Issue node_id missing from REST response.")
        add_mut = """
            mutation($projectId:ID!, $contentId:ID!) {
            add: addProjectV2ItemById(input:{projectId:$projectId, contentId:$contentId}) {
                item { id }
            }
            }
        """
        add_res = self._graphql(add_mut, {"projectId": project_id, "contentId": issue_node_id})
        item_id = add_res["data"]["add"]["item"]["id"]

        # Prepare values
        values = cfg.get("fields", {})
        tzname = cfg.get("tz", "Canada/Mountain")
        if any(str(v).lower() == "iso-week" for v in values.values()):
            week_num = datetime.now(pytz.timezone(tzname)).isocalendar().week

        for name, value in values.items():
            f = fields.get(name)
            if not f:
                print(f"Project field '{name}' not found; skipping.")
                continue

            # Build "value" for the mutation based on dataType
            dataType = f["dataType"]
            if isinstance(value, str) and value.lower() == "iso-week":
                value = week_num

            if dataType == "SINGLE_SELECT":
                # Map option name -> id
                opt = next((o for o in f.get("options", []) if o["name"] == str(value)), None)
                if not opt:
                    print(f"Option '{value}' not found for '{name}'; skipping.")
                    continue
                payload = {"singleSelectOptionId": opt["id"]}
            elif dataType == "NUMBER":
                payload = {"number": float(value)}
            else:
                # TEXT, DATE, ITERATION, etc. -> keep simple: text
                payload = {"text": str(value)}

            mut = """
            mutation($projectId:ID!, $itemId:ID!, $fieldId:ID!, $value:ProjectV2FieldValue!){
              update: updateProjectV2ItemFieldValue(
                input:{projectId:$projectId, itemId:$itemId, fieldId:$fieldId, value:$value}
              ){ clientMutationId }
            }"""
            self._graphql(mut, {
                "projectId": project_id,
                "itemId": item_id,
                "fieldId": f["id"],
                "value": payload,
            })

    def _get_project(self, owner: str, number: int | None, title: str | None):
        """
        Resolve a Projects v2 project by number (best) or title.
        Tries user first, then organization.
        Returns: { id, fields: [ {id,name,dataType,options?} ] }
        """
        owner_type = (self.project_cfg or {}).get("owner_type", "org")
        base_fields = """
            id
            title
            fields(first: 100) {
                nodes {
                ... on ProjectV2FieldCommon {
                    id
                    name
                    dataType
                }
                ... on ProjectV2SingleSelectField {
                    id
                    name
                    dataType
                    options { id name }
                }
                }
            }
        """

        def q_by_number(scope):
            return f"""
            query($login:String!, $number:Int!){{
            {scope}(login:$login) {{
                projectV2(number:$number) {{ {base_fields} }}
            }}
            }}"""

        def q_by_title(scope):
            return f"""
            query($login:String!, $query:String!){{
            {scope}(login:$login) {{
                projectsV2(first: 50, query:$query) {{ nodes {{ {base_fields} }} }}
            }}
            }}"""

        scopes = [("organization",), ("user",)]
        # respect owner_type, but still fall back to the other if not found
        if owner_type == "user":
            scopes = [("user",), ("organization",)]

        if number is not None:
            for (scope,) in scopes:
                data = self._graphql(q_by_number(scope), {"login": owner, "number": int(number)})["data"]
                node = (data.get(scope) or {}).get("projectV2")
                if node:
                    return {"id": node["id"], "fields": node["fields"]["nodes"]}
            raise RuntimeError("Project not found by number for owner.")
        # title path
        for (scope,) in scopes:
            data = self._graphql(q_by_title(scope), {"login": owner, "query": title or ""})["data"]
            nodes = ((data.get(scope) or {}).get("projectsV2") or {}).get("nodes", [])
            node = next((n for n in nodes if (title or "").lower() == n.get("title", "").lower()), None)
            if node:
                return {"id": node["id"], "fields": node["fields"]["nodes"]}
        raise RuntimeError("Project not found by title for owner.")

    def _graphql(self, query: str, variables: dict):
        resp = requests.post(
            "https://api.github.com/graphql",
            headers={"Authorization": f"bearer {self.gh_token}",
                     "Accept": "application/vnd.github+json"},
            json={"query": query, "variables": variables},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"GraphQL HTTP {resp.status_code}: {resp.text}")
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        return data