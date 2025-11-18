import subprocess

def validate_results(scheme, user_results):
    total_score = 0
    feedback = []

    for criterion in scheme["criteria"]:
        if criterion["type"] == "command":
            command_result = subprocess.getoutput(criterion["command"])
            if criterion["expected"] in command_result:
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
    return total_score, feedback
