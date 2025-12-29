import subprocess
import os
import requests
import urllib.parse

GITLAB_URL = os.getenv("GITLAB_URL")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")

def get_dynamic_suffix(group_num, class_type):
    # Hasilnya: kelompok1-sija1
    suffix_standard = f"kelompok{group_num}-sija{class_type}"

    # Hasilnya: kelompok1sija1 (tanpa strip, buat kriteria tertentu)
    suffix_clean = f"kelompok{group_num}sija{class_type}"
    return suffix_standard, suffix_clean

def check_gitlab_project(path_with_namespace: str):
    """
    Cek apakah project dengan path_with_namespace tertentu ada di GitLab.
    Contoh path_with_namespace: 'kelompokx-sijax/build-image-kelompokx-sijax'
    """
    gitlab_url = os.getenv("GITLAB_URL")
    gitlab_token = os.getenv("GITLAB_TOKEN")

    if not gitlab_token or not gitlab_url:
        return False, "GitLab env not set"

    headers = {"PRIVATE-TOKEN": gitlab_token}

    encoded = urllib.parse.quote(path_with_namespace, safe="")
    url = f"{gitlab_url.rstrip('/')}/api/v4/projects/{encoded}"

    try:
        r = requests.get(url, headers=headers, timeout=10)
        print(
            "DEBUG check_gitlab_project:",
            "url=", url,
            "status=", r.status_code,
            "text=", r.text[:200],
            flush=True
        )

        if r.status_code == 200:
            return True, "project_exists"
        elif r.status_code == 404:
            return False, "project_not_found"
        elif r.status_code == 500:
            # Workaround: fallback pakai search
            search_url = f"{gitlab_url.rstrip('/')}/api/v4/projects"
            ns, _, name = path_with_namespace.partition("/")
            params = {"search": name}
            r2 = requests.get(search_url, headers=headers, params=params, timeout=10)
            print(
                "DEBUG fallback search:",
                "url=", search_url,
                "status=", r2.status_code,
                "text=", r2.text[:200],
                flush=True
            )

            if r2.status_code != 200:
                return False, f"GitLab API error {r2.status_code}"

            for proj in r2.json():
                if proj.get("path_with_namespace") == path_with_namespace:
                    return True, "project_exists"

            return False, "project_not_found"
        else:
            return False, f"GitLab API error {r.status_code}"
    except Exception as e:
        return False, str(e)


def get_gitlab_project_id(path_with_namespace: str):
    """
    Ambil project_id dari path_with_namespace.
    """
    ok, msg = check_gitlab_project(path_with_namespace)
    if not ok:
        return None, msg

    gitlab_url = os.getenv("GITLAB_URL")
    gitlab_token = os.getenv("GITLAB_TOKEN")
    headers = {"PRIVATE-TOKEN": gitlab_token}

    encoded = urllib.parse.quote(path_with_namespace, safe="")
    url = f"{gitlab_url.rstrip('/')}/api/v4/projects/{encoded}"

    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None, f"GitLab API error {r.status_code}"
        return r.json().get("id"), "ok"
    except Exception as e:
        return None, str(e)


def check_gitlab_pipeline(project_id, ref="main"):
    if not GITLAB_TOKEN or not GITLAB_URL:
        return False, "GitLab env not set"

    base = GITLAB_URL.rstrip('/')
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}

    # pipeline terbaru
    r = requests.get(
        f"{base}/api/v4/projects/{project_id}/pipelines",
        headers=headers,
        params={"ref": ref, "per_page": 1},
        timeout=10,
    )
    if r.status_code != 200:
        return False, f"GitLab API error {r.status_code}"

    data = r.json()
    if not data:
        return False, "No pipeline found"

    pipeline_id = data[0]["id"]

    # jobs di pipeline itu
    r2 = requests.get(
        f"{base}/api/v4/projects/{project_id}/pipelines/{pipeline_id}/jobs",
        headers=headers,
        timeout=10,
    )
    if r2.status_code != 200:
        return False, f"GitLab API error {r2.status_code} (jobs)"

    jobs = r2.json()
    for job in jobs:
        if job.get("name") == "build-image":
            status = job.get("status")
            return status == "success", f"job_status={status}"

    return False, "job_not_found(build-image)"

def get_latest_pipeline_and_jobs(project_id, ref="main"):
    """
    Ambil pipeline terbaru + list jobs-nya.
    """
    if not GITLAB_TOKEN or not GITLAB_URL:
        return None, None, "GitLab env not set"

    base = GITLAB_URL.rstrip('/')
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}

    # pipeline terbaru
    r = requests.get(
        f"{base}/api/v4/projects/{project_id}/pipelines",
        headers=headers,
        params={"ref": ref, "per_page": 1},
        timeout=10
    )
    if r.status_code != 200:
        return None, None, f"GitLab API error {r.status_code} (pipelines)"

    data = r.json()
    if not data:
        return None, None, "No pipeline found"

    pipeline = data[0]
    pipeline_id = pipeline["id"]

    # jobs di pipeline itu
    r2 = requests.get(
        f"{base}/api/v4/projects/{project_id}/pipelines/{pipeline_id}/jobs",
        headers=headers,
        timeout=10
    )
    if r2.status_code != 200:
        return pipeline, None, f"GitLab API error {r2.status_code} (jobs)"

    return pipeline, r2.json(), "ok"


def check_gitlab_runner(path_with_namespace: str, expected_name: str, ref="main"):
    """
    Cek apakah job 'build-image' di pipeline terbaru dijalankan oleh runner
    yang description-nya mengandung expected_name.
    """
    project_id, msg = get_gitlab_project_id(path_with_namespace)
    if not project_id:
        return False, msg

    pipeline, jobs, msg2 = get_latest_pipeline_and_jobs(project_id, ref=ref)
    if not jobs:
        return False, msg2

    for job in jobs:
        if job.get("name") == "build-image":
            runner = job.get("runner") or {}
            description = runner.get("description", "")
            if expected_name in description:
                return True, f"runner_ok({description})"
            else:
                return False, f"runner_mismatch({description})"

    return False, "job_not_found"

def check_gitlab_pipeline_two_success(project_id, ref="main"):
    if not GITLAB_TOKEN or not GITLAB_URL:
        return False, "GitLab env not set"

    base = GITLAB_URL.rstrip('/')
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}

    r = requests.get(
        f"{base}/api/v4/projects/{project_id}/pipelines",
        headers=headers,
        params={"ref": ref, "per_page": 1},
        timeout=10,
    )
    if r.status_code != 200:
        return False, f"GitLab API error {r.status_code}"

    data = r.json()
    if not data:
        return False, "No pipeline found"

    pipeline_id = data[0]["id"]

    r2 = requests.get(
        f"{base}/api/v4/projects/{project_id}/pipelines/{pipeline_id}/jobs",
        headers=headers,
        timeout=10,
    )
    if r2.status_code != 200:
        return False, f"GitLab API error {r2.status_code} (jobs)"

    jobs = r2.json()
    success_jobs = [
        j for j in jobs
        if j.get("status") == "success" and j.get("stage") in ("staging", "production")
    ]
    count = len(success_jobs)

    return count >= 2, f"success_jobs={count}"

def check_gitlab_pipeline_min_success(project_id, ref="main", min_count=3):
    if not GITLAB_TOKEN or not GITLAB_URL:
        return False, "GitLab env not set"

    base = GITLAB_URL.rstrip('/')
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}

    r = requests.get(
        f"{base}/api/v4/projects/{project_id}/pipelines",
        headers=headers,
        params={"ref": ref, "per_page": 1},
        timeout=10,
    )
    if r.status_code != 200:
        return False, f"GitLab API error {r.status_code}"

    data = r.json()
    if not data:
        return False, "No pipeline found"

    pipeline_id = data[0]["id"]

    r2 = requests.get(
        f"{base}/api/v4/projects/{project_id}/pipelines/{pipeline_id}/jobs",
        headers=headers,
        timeout=10,
    )
    if r2.status_code != 200:
        return False, f"GitLab API error {r2.status_code} (jobs)"

    jobs = r2.json()
    # Filter job yang sukses di stage build, staging, atau production
    success_jobs = [
        j for j in jobs
        if j.get("status") == "success" and j.get("stage") in ("build", "staging", "production")
    ]
    count = len(success_jobs)

    return count >= min_count, f"success_jobs={count}"

def validate_results(scheme, user_results):
    total_score = 0
    feedback = []

    for criterion in scheme["criteria"]:
        ctype = criterion["type"]

        if ctype == "command":
            output = subprocess.getoutput(criterion["command"])
            if criterion["expected"] in output:
                total_score += criterion["score"]
            else:
                feedback.append(criterion["description"])

        elif ctype == "file":
            try:
                with open(criterion["path"], "r") as f:
                    content = f.read()
                    if criterion["contains"] in content:
                        total_score += criterion["score"]
                    else:
                        feedback.append(criterion["description"])
            except FileNotFoundError:
                feedback.append(f"File {criterion['path']} not found")

        elif ctype == "gitlab_project":
            ok, msg = check_gitlab_project(criterion["key"])
            if ok:
                total_score += criterion["score"]
            else:
                feedback.append(f"{criterion['description']} ({msg})")

        elif ctype == "gitlab_pipeline":
            # pakai 'key' = path_with_namespace, bukan project_id manual
            project_id, msg = get_gitlab_project_id(criterion["key"])
            if not project_id:
                feedback.append(f"{criterion['description']} ({msg})")
                continue

            ok, msg2 = check_gitlab_pipeline(
                project_id=project_id,
                ref=criterion.get("ref", "main"),
            )
            if ok:
                total_score += criterion["score"]
            else:
                feedback.append(f"{criterion['description']} ({msg2})")

    return total_score, feedback
