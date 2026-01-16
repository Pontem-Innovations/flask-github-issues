import hashlib
import requests
from datetime import datetime
import pytz
import re
from urllib.parse import urlparse
import ipaddress
import sys
import json

try:
    from flask import has_request_context, request
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

try:
    from user_agents import parse as parse_ua
    HAS_UA_PARSER = True
except ImportError:
    HAS_UA_PARSER = False

# Default sensitive field names to redact from request bodies
DEFAULT_SENSITIVE_FIELDS = [
    "password", "passwd", "pwd",
    "token", "access_token", "refresh_token", "auth_token",
    "secret", "client_secret",
    "api_key", "apikey",
    "authorization", "auth",
    "credit_card", "card_number", "cvv", "cvc",
    "ssn", "social_security",
]


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
        # Request body capture settings
        self.capture_body = True
        self.body_max_length = 2000
        self.sensitive_fields = DEFAULT_SENSITIVE_FIELDS.copy()
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

        # Request body capture settings (opt-out, enabled by default)
        self.capture_body = app.config.get("GH_CAPTURE_BODY", True)
        self.body_max_length = app.config.get("GH_BODY_MAX_LENGTH", 2000)
        self.sensitive_fields = app.config.get("GH_SENSITIVE_FIELDS", DEFAULT_SENSITIVE_FIELDS.copy())

        if not self.gh_token or not self.gh_repo:
            raise ValueError("GH_TOKEN and GH_REPO must be set in configuration.")
        app.extensions["error_tracking"] = self

    # ────────────────────────────── public API ────────────────────────────────
    def track_error(self, *, error_message: str, details: list[dict] | None = None):
        """
        Log an error. `details` is a list of single‑key dicts that will be
        rendered in the issue body, e.g. `[{'User Email': 'a@b.com'}, {'URL': '/foo'}]`.
        
        Request context (Method, URL, Device, OS, Browser, Body) is automatically
        captured when called within a Flask request context. User-provided details
        override auto-captured values for the same keys.
        """
        if not error_message:
            print("Error message is required.")
            return

        details = details or []
        
        # Auto-capture request context and merge with user-provided details
        auto_captured = self._capture_request_context()
        if auto_captured:
            # Get keys that user explicitly provided
            user_keys = {k for d in details for k in d.keys()}
            # Only add auto-captured fields that user didn't override
            for auto_detail in auto_captured:
                for key in auto_detail:
                    if key not in user_keys:
                        details.append(auto_detail)

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
        # Categorize details into sections
        request_keys = {"Method", "URL", "Body"}
        client_keys = {"Device", "OS", "Browser"}
        
        request_info = []
        client_info = []
        context_info = []
        
        for d in details:
            for k, v in d.items():
                if k in request_keys:
                    request_info.append((k, v))
                elif k in client_keys:
                    client_info.append((k, v))
                else:
                    context_info.append((k, v))
        
        sections = [f"{prefix}**Timestamp:** {ts}"]
        
        # Request Info section
        if request_info:
            # Sort to ensure consistent order: Method, URL, Body
            order = {"Method": 0, "URL": 1, "Body": 2}
            request_info.sort(key=lambda x: order.get(x[0], 99))
            sections.append("\n**Request Info:**")
            for k, v in request_info:
                if k == "Body" and v:
                    sections.append(f"**{k}:**\n```json\n{v}\n```")
                else:
                    sections.append(f"**{k}:** {v}")
        
        # Client Info section
        if client_info:
            # Sort to ensure consistent order: Device, OS, Browser
            order = {"Device": 0, "OS": 1, "Browser": 2}
            client_info.sort(key=lambda x: order.get(x[0], 99))
            sections.append("\n**Client Info:**")
            for k, v in client_info:
                sections.append(f"**{k}:** {v}")
        
        # Context section (user-provided details)
        if context_info:
            sections.append("\n**Context:**")
            for k, v in context_info:
                sections.append(f"**{k}:** {v}")
        
        # Error message
        if err:
            sections.append(f"\n**Error Message:**\n```{err}```")
        
        return "\n".join(sections)

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

    # ───────────────────── request context capture ────────────────────────────
    def _capture_request_context(self) -> list[dict]:
        """
        Auto-capture request context when inside a Flask request.
        Returns a list of single-key dicts for: Method, URL, Device, OS, Browser, Body.
        """
        if not HAS_FLASK or not has_request_context():
            return []
        
        captured = []
        
        # Method
        captured.append({"Method": request.method})
        
        # URL
        captured.append({"URL": request.url})
        
        # User-Agent parsing (Device, OS, Browser)
        ua_string = request.headers.get("User-Agent", "")
        if ua_string:
            ua_details = self._parse_user_agent(ua_string)
            for key, value in ua_details.items():
                if value:  # Only add non-empty values
                    captured.append({key: value})
        
        # Request body (only for methods that typically have a body)
        if self.capture_body and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            body = self._get_request_body()
            if body:
                captured.append({"Body": body})
        
        return captured
    
    @staticmethod
    def _parse_user_agent(ua_string: str) -> dict:
        """
        Parse User-Agent string into Device, OS, and Browser components.
        Returns dict with keys: Device, OS, Browser (values may be empty strings).
        """
        if not HAS_UA_PARSER or not ua_string:
            return {"Device": "", "OS": "", "Browser": ""}
        
        try:
            ua = parse_ua(ua_string)
            
            # Device: family + brand + model
            device_parts = []
            if ua.device.family and ua.device.family != "Other":
                device_parts.append(ua.device.family)
            if ua.device.brand:
                device_parts.append(ua.device.brand)
            if ua.device.model:
                device_parts.append(ua.device.model)
            device = " ".join(device_parts) if device_parts else "Desktop/Unknown"
            
            # Determine device type
            if ua.is_mobile:
                device = f"Mobile - {device}"
            elif ua.is_tablet:
                device = f"Tablet - {device}"
            elif ua.is_pc:
                device = f"Desktop - {device}" if device != "Desktop/Unknown" else "Desktop"
            elif ua.is_bot:
                device = f"Bot - {device}"
            
            # OS: family + version
            os_parts = [ua.os.family] if ua.os.family else []
            if ua.os.version_string:
                os_parts.append(ua.os.version_string)
            os_str = " ".join(os_parts) if os_parts else "Unknown"
            
            # Browser: family + version
            browser_parts = [ua.browser.family] if ua.browser.family else []
            if ua.browser.version_string:
                browser_parts.append(ua.browser.version_string)
            browser = " ".join(browser_parts) if browser_parts else "Unknown"
            
            return {"Device": device, "OS": os_str, "Browser": browser}
        except Exception:
            return {"Device": "", "OS": "", "Browser": ""}
    
    def _get_request_body(self) -> str:
        """
        Get sanitized request body (JSON or form data).
        Redacts sensitive fields and truncates to max length.
        """
        try:
            # Try JSON first
            if request.is_json:
                body_data = request.get_json(silent=True)
                if body_data:
                    sanitized = self._sanitize_data(body_data)
                    return self._truncate_body(json.dumps(sanitized, indent=2, default=str))
            
            # Try form data
            if request.form:
                form_dict = dict(request.form)
                sanitized = self._sanitize_data(form_dict)
                return self._truncate_body(json.dumps(sanitized, indent=2, default=str))
            
            # Raw data (for other content types)
            raw = request.get_data(as_text=True)
            if raw:
                return self._truncate_body(raw)
        except Exception:
            pass
        
        return ""
    
    def _sanitize_data(self, data):
        """
        Recursively redact sensitive fields from dicts/lists.
        """
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                # Check if key matches any sensitive field (case-insensitive)
                if any(s.lower() in key.lower() for s in self.sensitive_fields):
                    result[key] = "***REDACTED***"
                else:
                    result[key] = self._sanitize_data(value)
            return result
        elif isinstance(data, list):
            return [self._sanitize_data(item) for item in data]
        else:
            return data
    
    def _truncate_body(self, body: str) -> str:
        """
        Truncate body to max length with indicator.
        """
        if len(body) <= self.body_max_length:
            return body
        return body[:self.body_max_length] + "\n... [truncated]"

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