# flask-github-issues

flask-github-issues is an extension for <a href='https://flask.palletsprojects.com/'>Flask</a> that makes it easy to send unhandled errors directly from your application to GitHub Iusses.

# An Example: 

`pip install flask_github_issues`

extensions.py
```
from flask_github_issues import ErrorTracking

gitub_error_tracking = ErrorTracking()
```

__init__.py
```
from flask import Flask, request, session
import traceback

from .extensions import gitub_error_tracking

def create_app(config_env=os.getenv("ENV"), register_blueprints=True):
    app = Flask(__name__)

    gitub_error_tracking.init_app(app)


@app.errorhandler(Exception)
    def internal_server_error(e):
        if request.path.startswith("/static/"):
            # Return a simple 500 response without rendering a template
            return "", 500
        else:
            
            error_msg = traceback.format_exc()
            user_email = session.get("email", "unknown")
            github_error_tracking.track_error(error_message=str(error_msg),
                details=[
                    {"User Email": user_email},
                    {"URL": request.url},
                    {"Tenant": "TEST"},
                ],
            )
            return render_template("500.html"), 500
```

config.py:

```
class Config:
    GH_TOKEN = os.getenv("GH_TOKEN")
    GH_REPO = os.getenv("GH_REPO")
    GH_ASSIGNEES = ["ghawes85"]
    GH_LABELS = ["bug"]
    GH_TYPES = "bug" # Types is being released in the GitHub API. Currently if it is rolled out for you or your org, then can add one type
```

- Repo is in the form org/repo_name or user/repo_name
- Token is a Github <a href='https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens'>fine-grained personal access token</a>
- Assignees is a list of of users you want the ticket to be assigned to
- Labels is a list of labels you want assigned to your ticket

As an example and using the follow route: 
```
@app.route("/error")
    def error():
        raise ValueError("This is a test Value ERROR")
```

The following issue is created in GitHub
![image](https://github.com/user-attachments/assets/8a731241-b1b8-45bc-a9e6-362de0417a6d)
