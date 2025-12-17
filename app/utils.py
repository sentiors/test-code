import subprocess
import os
import requests

GITLAB_URL = os.getenv("GITLAB_URL")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")

def check_gitlab_pipeline(project_id, ref="main"):
    if not GITLAB_TOKEN or not GITLAB_URL:
        return False, "GitLab env not set"

    url = f"{GITLAB_URL}/api/v4/projects/{project_id}/pipelines"
    headers = {
        "PRIVATE-TOKEN": GITLAB_TOKEN
    }
    params = {
        "ref": ref,
        "per_page": 1
    }

    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            return False, f"GitLab API error {r.status_code}"

        data = r.json()
        if not data:
            return False, "No pipeline found"

        status = data[0]["status"]
        return status == "success", f"pipeline_status={status}"

    except Exception as e:
        return False, str(e)


def validate_results(scheme, user_results):
    total_score = 0
    feedback = []

    for criterion in scheme["criteria"]:
        if criterion["type"] == "command":
            output = subprocess.getoutput(criterion["command"])
            if criterion["expected"] in output:
                total_score += criterion["score"]
            else:
                feedback.append(criterion["description"])

        elif criterion["type"] == "file":
            try:
                with open(criterion["path"], 'r') as f:
                    content = f.read()
                    if criterion["contains"] in content:
                        total_score += criterion["score"]
                    else:
                        feedback.append(criterion["description"])
            except FileNotFoundError:
                feedback.append(f"File {criterion['path']} not found")

        elif criterion["type"] == "gitlab_pipeline":
            ok, msg = check_gitlab_pipeline(
                project_id=criterion["project_id"],
                ref=criterion.get("ref", "main")
            )
            if ok:
                total_score += criterion["score"]
            else:
                feedback.append(f"{criterion['description']} ({msg})")

    return total_score, feedback
