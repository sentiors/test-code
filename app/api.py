from flask import Flask, request, jsonify, render_template, make_response, redirect, url_for, flash, session
import os
import urllib.parse as urlparse
import requests
import json
from .utils import (
    check_gitlab_pipeline,
    check_gitlab_project,
    check_gitlab_runner,
    get_gitlab_project_id,
    check_gitlab_pipeline_two_success,
    check_gitlab_pipeline_min_success
)
from .utils_grafana import (
    check_grafana_alert_rule,
    check_grafana_alert_history,
    check_grafana_health,
    check_dashboard_folder,
    check_datasource,
)
import smtplib
import uuid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from .utils_gmail import check_alert_email_sent_any, send_otp_gmail
from .database import db_session, init_db
from .models import User, Lab, GradingResult, LabSession, Group, Class, Admin
from datetime import datetime
import pytz
wib = pytz.timezone("Asia/Jakarta")
timestamp = datetime.now(wib)
import csv
from io import StringIO
import subprocess
import re
import random
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = 'sijagoan'

SCHEME_PATH = "/opt/grading/app/schemes/"
GITLAB_URL = "https://gitlab.smkn1cibinong.sch.id"
ACTIVE_LABS = {}

#==============================(gitlab)====================================#
def get_latest_pipeline(project_id, ref="main"):
    url = f"{GITLAB_URL}/api/v4/projects/{project_id}/pipelines"
    headers = {"Authorization": f"Bearer {GITLAB_TOKEN}"}

    r = requests.get(url, headers=headers, params={"ref": ref})
    return r.json()[0]

#==============================(api-gmail)====================================#
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "https://grading.smkn1cibinong.sch.id/oauth2callback")


@app.route("/auth/google")
def auth_google():
    # Redirect user ke halaman login Google
    base = "https://accounts.google.com/o/oauth2/v2/auth"
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "https://mail.google.com/",
        "access_type": "offline",  # supaya dapat refresh_token
        "prompt": "consent",       # paksa consent biar refresh_token keluar
    }
    url = base + "?" + urlparse.urlencode(params)
    return redirect(url)


@app.route("/oauth2callback")
def oauth2callback():
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        return f"OAuth error: {error}", 400
    if not code:
        return "Missing code", 400

    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }

    resp = requests.post(token_url, data=data, timeout=10)
    if resp.status_code != 200:
        return f"Token exchange failed: {resp.status_code} {resp.text}", 400

    tokens = resp.json()
    # Penting: tokens["refresh_token"]
    print("DEBUG GOOGLE TOKENS:", tokens, flush=True)

    # TODO: simpan refresh_token ke DB/file yang aman
    # sementara balikin ke browser dulu (nanti jangan di-production)
    return jsonify({
        "message": "OAuth success, tokens received",
        "tokens": tokens,
    })

def validate_input(email, phone):
    # Validasi email harus diakhiri @gmail.com
    if not re.match(r"^[a-zA-Z0-9._%+-]+@gmail\.com$", email):
        return "Email harus menggunakan domain @gmail.com"

    # Validasi nomor telepon harus dimulai dengan 62 dan hanya angka
    if not re.match(r"^62[0-9]+$", phone):
        return "Nomor telepon harus dimulai dengan 62 (contoh: 62812...)"

    return None

def sync_labs_from_schemes():
    """
    Sinkronkan tabel 'labs' dengan semua file JSON di SCHEME_PATH.
    lab_id = nama file tanpa ekstensi .json
    """
    scheme_files = glob.glob(os.path.join(SCHEME_PATH, "*.json"))

    # Ambil lab_id yang sudah ada di DB
    existing = {lab.lab_id for lab in db_session.query(Lab.lab_id).all()}

    for path in scheme_files:
        filename = os.path.basename(path)
        lab_id, ext = os.path.splitext(filename)
        if ext != ".json":
            continue
        if lab_id not in existing:
            db_session.add(Lab(lab_id=lab_id, scheme_path=path))
            existing.add(lab_id)

    db_session.commit()

def load_static_users():
    # Sesuaikan path agar menunjuk ke /opt/grading/app/users.json
    file_path = "/opt/grading/app/users.json"
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                data = json.load(f)
                # Jika formatnya {"username": "admin", "password": "..."}
                if "username" in data:
                    return {data["username"]: data["password"]}
                return data
    except Exception as e:
        print(f"Error loading users: {e}")
    
    return {"admin": "SIJAGOAN"} # Fallback jika file rusak/tidak ada

def run_cleanup_actions(lab_id, username):
    import re
    import subprocess

    print(f"\n=== START CLEANUP DEBUG FOR {username} ===")
    scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")

    with open(scheme_file, "r") as f:
        scheme = json.load(f)

    user = db_session.query(User).filter_by(username=username).first()
    if not user:
        print(f"DEBUG ERROR: User {username} tidak ada di database!")
        return

    # Pastikan suffix bersih
    group = user.group_name.lower().strip().replace(" ", "")
    kelas = user.class_name.lower().strip().replace(" ", "")
    suffix = f"{group}-{kelas}"

    print(f"DEBUG: Suffix yang dibentuk -> '{suffix}'")

    for criterion in scheme.get("criteria", []):
        if criterion.get("cleanup") is True:
            ctype = criterion.get("type")
            key = criterion.get("key")

            # Kita pakai replace manual yang lebih pasti untuk debug
            # Karena di JSON kamu: cirros-cli-kelompokx-sijax
            real_name = key.replace("kelompokx-sijax", suffix)

            print(f"DEBUG: Mencoba hapus {ctype} -> Nama Asli: {key} | Nama Target: {real_name}")

            if ctype == "instance":
                # Jalankan perintah dan tangkap outputnya
                cmd = ["/usr/bin/openstack", "server", "delete", real_name]
                print(f"DEBUG: Menjalankan perintah -> {' '.join(cmd)}")

                result = subprocess.run(cmd, capture_output=True, text=True)

                if result.returncode == 0:
                    print(f"DEBUG SUCCESS: VM {real_name} berhasil dihapus atau sedang proses.")
                else:
                    print(f"DEBUG FAILED: Alasan gagal -> {result.stderr.strip()}")

    print("=== END CLEANUP DEBUG ===\n")

BASE_DIR = "/opt/grading"

@app.route('/create-node', methods=['POST'])
def create_node():
    data = request.json
    name = data.get('name')
    node_type = data.get('type') # 'file' atau 'folder'
    if not name:
        return jsonify({"success": False, "error": "Nama tidak boleh kosong"})

    target_path = os.path.join("/opt/grading", name)
    try:
        if node_type == 'file':
            if not os.path.exists(target_path):
                with open(target_path, 'w') as f:
                    f.write("")
            else:
                return jsonify({"success": False, "error": "File sudah ada"})
        else:
            os.makedirs(target_path, exist_ok=True)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/list-files')
def list_files():
    files_data = []
    # Kita scan dari folder root project
    root_dir = "/opt/grading"

    # Daftar folder yang ingin kita ikutkan dalam list
    allowed_folders = ['app', 'schemes', 'templates']
    # Daftar ekstensi yang boleh diedit
    allowed_extensions = ('.py', '.html', '.json', '.js', '.css', '.txt', '.yml')

    for root, dirs, files in os.walk(root_dir):
        # Abaikan folder sampah agar tidak berat
        if any(x in root for x in ["__pycache__", ".git", "logs", "case"]):
            continue

        for name in files:
            # Saring: Hanya ambil file script dan abaikan file backup (.save / .backup)
            if name.endswith(allowed_extensions) and not any(x in name for x in [".save", ".backup", ".tmp", "pyc"]):
                # Ambil path relatif dari /opt/grading
                rel_path = os.path.relpath(os.path.join(root, name), root_dir)
                files_data.append(rel_path)

    return jsonify(sorted(files_data))

@app.route('/get-file-content')
def get_file_content():
    path = request.args.get('path')
    full_path = os.path.join(BASE_DIR, path)
    if os.path.exists(full_path):
        with open(full_path, 'r') as f:
            return jsonify({"content": f.read()})
    return jsonify({"error": "File not found"}), 404

@app.route('/save-file-content', methods=['POST'])
def save_file_content():
    data = request.json
    full_path = os.path.join(BASE_DIR, data['path'])
    try:
        with open(full_path, 'w') as f:
            f.write(data['content'])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.before_request
def validate_content_type():
    # 1. Biarkan GET request lewat (untuk nampilin halaman)
    if request.method != 'POST':
        return

    # 2. JALUR VIP: Jika mengakses login, jangan cek Content-Type sama sekali
    # Kita pakai request.path agar lebih akurat
    if request.path == '/login' or request.endpoint == 'login_web':
        return

    # 3. Untuk sisanya (API), cek apakah JSON
    # Kita gunakan cara manual agar tidak memicu error 415 otomatis dari Flask
    content_type = request.headers.get('Content-Type', '')
    if 'application/json' not in content_type:
        return jsonify({
            "error": "Server error",
            "details": "Content-Type harus berupa application/json"
        }), 415

@app.route('/register', methods=['POST'])
def register():
    try:
        data = request.json
        # Ambil data dan bersihkan dari spasi liar serta paksa huruf kecil untuk email
        name = data.get('name', '').strip()
        email = data.get('email', '').strip().lower()
        phone = data.get('phone', '').strip()
        username = data.get('username', '').strip()
        password = data.get('password', '')
        classname = data.get('class_name', '').strip()
        groupname = data.get('group_name', '').strip()

        # Validasi Email (harus @gmail.com dan sudah di-strip)
        if not re.match(r"^[a-z0-9._%+-]+@gmail\.com$", email):
            return jsonify({"error": f"Email '{email}' tidak valid! Pastikan menggunakan @gmail.com tanpa spasi."}), 400

        # Validasi Phone (harus mulai 62)
        if not re.match(r"^62[0-9]+$", phone):
            return jsonify({"error": "Nomor telepon harus dimulai dengan 62 dan hanya angka!"}), 400

        # Cek Username apakah sudah ada
        exists = db_session.query(User).filter_by(username=username).first()
        if exists:
            return jsonify({"error": f"Username '{username}' sudah digunakan!"}), 400

        # Simpan ke DB
        new_user = User(
            username=username,
            password=password,
            name=name,
            email=email,
            phone=phone,
            class_name=classname,
            group_name=groupname
        )
        db_session.add(new_user)
        db_session.commit()
        return jsonify({"message": "Registrasi berhasil!"}), 201

    except Exception as e:
        db_session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.json
        username = data.get("username")
        password = data.get("password")

        # Validasi input
        if not username or not password:
            return jsonify({"error": "Username password dibutuhkan"}), 400

        # Cari user di database
        user = db_session.query(User).filter(User.username == username).first()
        if not user:
            return jsonify({"error": "User tidak ditemukan, silakan registrasi!"}), 404

        # Validasi password
        if user.password != password:
            return jsonify({"error": "Password salah!"}), 401

        # Buat token (contoh sederhana)
        token = f"dummy-token-kelompok{user.group_name}-{user.class_name}-{username}"
        return jsonify({"token": token, "class_name": user.class_name}), 200

    except Exception as e:
        print(f"Error in login: {str(e)}")  # Debugging
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route('/start-lab', methods=['POST'])
def start_lab():
    try:
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "")

        # FIX 1: Ambil username dari token dulu!
        username = token.split("-")[-1]

        # FIX 2: Cari user dulu buat dapet group_name
        user = db_session.query(User).filter_by(username=username).first()
        if not user: return jsonify({"error": "User tidak ditemukan"}), 404

        data = request.get_json()
        lab_id = data.get("lab_id")

        lab_exists = db_session.query(Lab).filter_by(lab_id=lab_id).first()
        if not lab_exists:
            return jsonify({"error": f"Lab '{lab_id}' tidak terdaftar dalam sistem!"}), 404

        # Shared Timer Logic
        session = db_session.query(LabSession).filter_by(group_name=user.group_name, lab_id=lab_id).first()
        if not session:
            session = LabSession(group_name=user.group_name, lab_id=lab_id, first_start=datetime.now(wib))
            db_session.add(session)
            db_session.commit()

        # Simpan lab yang aktif untuk token ini
        ACTIVE_LABS[token] = {"lab_id": lab_id, "status": "active", "start_time": session.first_start}

        return jsonify({"message": f"Lab '{lab_id}' berhasil dimulai!"}), 200

    except Exception as e:
        print(f"Error in start_lab: {str(e)}")  # Debugging
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route('/get-scheme-description', methods=['GET'])
def get_scheme_description():
    lab_id = request.args.get("lab_id")
    scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")

    if not os.path.exists(scheme_file):
        return jsonify({"error": "Lab tidak ditemukan"}), 404

    with open(scheme_file, 'r') as f:
        scheme = json.load(f)

    # Format deskripsi skema
    description = {
        "lab_id": lab_id,
        "description": scheme.get("description", "No description available"),
        "criteria": [
            {
                "type": criterion.get("type"),
                "description": criterion.get("description"),
                "key": criterion.get("key")
            }
            for criterion in scheme.get("criteria", [])
        ]
    }

    return jsonify(description), 200

@app.route('/get-scheme', methods=['GET'])
def get_scheme():
    lab_id = request.args.get("lab_id")
    scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")

    if not os.path.exists(scheme_file):
        return jsonify({"error": "Lab tidak ditemukan"}), 404

    with open(scheme_file, 'r') as f:
        scheme = json.load(f)

    return jsonify({"scheme": scheme}), 200

from datetime import datetime
import pytz
wib = pytz.timezone("Asia/Jakarta")

@app.route('/grade-lab', methods=['POST'])
def grade_lab():
    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        data = request.get_json()
        if not data:
            return jsonify({"error": "Data permintaan tidak valid"}), 400

        lab_id = data.get("lab_id")
        class_name = data.get("class_name")
        client_data = data.get("client_data", {})

        try:
            username = token.split("-")[-1]
        except:
            return jsonify({"error": "Token tidak valid"}), 401

        # FIX 1: Gunakan satu nama variabel yang konsisten (user_info)
        user_info = db_session.query(User).filter_by(username=username).first()
        if not user_info:
            return jsonify({"error": "User tidak ditemukan"}), 404

        lab_obj = db_session.query(Lab).filter_by(lab_id=lab_id).first()
        # Mengutamakan default 'kelompok'
        grading_type = getattr(lab_obj, 'grading_type', 'kelompok')

        # FIX 2: Definisikan suffix di awal sebelum loop kriteria
        group = user_info.group_name.lower().strip().replace(" ", "")
        kelas = user_info.class_name.lower().strip().replace(" ", "")
        suffix = f"{group}-{kelas}"

        lab_log_path = f"/var/log/gradingctl/labs/{lab_id}.log"
        os.makedirs(os.path.dirname(lab_log_path), exist_ok=True)

        scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")
        if not os.path.exists(scheme_file):
            return jsonify({"error": "Lab tidak ditemukan"}), 404

        with open(scheme_file, 'r') as f:
            scheme = json.load(f)

        total_score = 0
        feedback_failed = []
        feedback_success = []

        # ========= LOGIKA PENILAIAN PER KRITERIA =========
        for criterion in scheme.get("criteria", []):
            ctype = criterion.get("type")
            key = criterion.get("key")
            expected = criterion.get("expected")
            description = criterion.get("description")
            score = criterion.get("score", 0)

            actual_value = client_data.get(key, None)
            failed = False

            if ctype in ["gitlab_project", "gitlab_pipeline"]:
                # Pastikan menggunakan variabel 'username' yang sudah didefinisikan di atas
                user = db_session.query(User).filter_by(username=username).first()

                if user:
                    import re
                    # Ambil angka dari "Grup 1" dan "10_sija1"
                    g_match = re.findall(r'\d+', user.group_name)
                    c_match = re.findall(r'\d+', user.class_name)

                    if g_match and c_match:
                        suffix = f"kelompok{g_match[0]}-sija{c_match[0]}"
                        # Buat key dinamis (kelompok1-sija1/...)
                        dynamic_key = key.replace("kelompokx-sijax", suffix)

                        # Ambil ID Project dari GitLab
                        project_id, err_msg = get_gitlab_project_id(dynamic_key)

                        if project_id:
                            if ctype == "gitlab_pipeline":
                                # Case 1: Cek Pipeline
                                ok, msg = check_gitlab_pipeline_two_success(project_id)
                                actual_value = "success" if ok else f"Fail: {msg}"
                            else:
                                # Case 2: Cek Project Name (Project found = OK)
                                actual_value = "OK"
                        else:
                            actual_value = f"Project {dynamic_key} Not Found ({err_msg})"
                    else:
                        actual_value = "Error: Group/Class format invalid"
                else:
                    actual_value = "Error: User info not found"

            elif ctype == "command":
                if str(actual_value) != str(expected):
                    failed = True
            elif ctype == "file_exists":
                if expected != str(actual_value):
                    failed = True
            elif ctype == "file_content":
                contains = criterion.get("contains")
                if not (contains and contains in str(actual_value)):
                    failed = True
            elif ctype == "service":
                if expected != str(actual_value):
                    failed = True
            elif ctype == "directory":
                if expected != str(actual_value):
                    failed = True
            elif ctype == "config_check":
                if not (expected and str(actual_value) == "correct"):
                    failed = True
            elif ctype == "package":
                if expected != str(actual_value):
                    failed = True
            elif ctype == "user":
                if expected != str(actual_value):
                    failed = True
            elif ctype == "group":
                if expected != str(actual_value):
                    failed = True
            elif ctype == "instance":
                # FIX 3: Gunakan variabel suffix yang sudah dibuat di atas
                real_vm_name = key.replace("kelompokx-sijax", suffix)
                # Logika penilaian instance biasanya mengecek apakah sudah dihapus (untuk cleanup)
                # atau apakah ada (untuk task awal). Sesuaikan dengan JSON kamu.
                if str(actual_value) != str(expected):
                    failed = True

            elif ctype == "gitlab_runner":
                ok, msg = check_gitlab_runner(
                    path_with_namespace=key,
                    expected_name=expected,
                    ref="main",
                )
                if not ok:
                    failed = True
                actual_value = msg
            elif ctype == "image":
                if expected != str(actual_value):
                    failed = True


            if not failed:
                total_score += score
                feedback_success.append(description)
            else:
                feedback_failed.append(f"{description}: Failed")

                #normalisasi pesan error
                msg = str(actual_value)
                if msg.startswith("Error: "):
                    msg = msg[len("Error: "):]

                print("DEBUG LOG WRITE:", lab_log_path, description, msg, flush=True)

                now_str = datetime.now(wib).strftime("%Y-%m-%d %H:%M:%S")

                with open(lab_log_path, 'a') as logfile:
                    logfile.write(
                        f"[{datetime.now(wib)}] CASE: {description} | ERROR: {msg}\n"
                    )

        # Kalau ada minimal 1 failed, nilai 0
        #if any("Failed" in f for f in feedback_failed):
        #    total_score = 0

        # Gabungkan: gagal dulu, lalu yang sukses
        all_feedback = feedback_failed + feedback_success

        print("DEBUG feedback_failed:", feedback_failed)
        print("DEBUG feedback_success:", feedback_success)
        print("DEBUG all_feedback:", all_feedback)
        print(f"Total score calculated: {total_score}")

        duration = 0
        penalty_messages = []
        
        session = db_session.query(LabSession).filter_by(group_name=user_info.group_name, lab_id=lab_id).first()
        if session:
            end_time = datetime.now(wib)
            duration = (end_time - session.first_start.replace(tzinfo=wib)).total_seconds()
            session.last_activity = end_time
            session.total_duration = duration

        # Cek apakah ada yang sudah dapat 100 di kelas ini
        is_first = db_session.query(GradingResult).filter_by(lab_id=lab_id, class_name=user_info.class_name, score=100).count() == 0
        
        # Pinalti Waktu (3% per 3 menit)
        max_duration = 180 
        if total_score > 0 and duration > max_duration:
            if is_first:
                penalty_messages.append("Bonus Pioneer: Pinalti diringankan karena solver yang pertama.")
                total_score = max(total_score, 95)
            else:
                n_penalty = int((duration - max_duration) // max_duration) + 1
                penalty_total = n_penalty * 3
                total_score = max(80, total_score * (100 - penalty_total) / 100)
                penalty_messages.append(f"Pinalti waktu: -{penalty_total}%")

        # Anti-Nyontek (Batas 80)
        if not is_first and duration < 60 and total_score > 90:
            total_score = 80 
            penalty_messages.append("Indikasi kecurangan: Nilai dibatasi ke 80.")

        # 5. Simpan Hasil (Prioritas Kelompok)
        def save_to_db(target_user):
            existing_res = db_session.query(GradingResult).filter_by(username=target_user, lab_id=lab_id).first()
            if existing_res:
                existing_res.score = total_score
                existing_res.feedback = ", ".join(all_feedback)
                existing_res.duration = duration
                existing_res.timestamp = datetime.now(wib)
            else:
                res = GradingResult(
                    username=target_user,
                    class_name=user_info.class_name,
                    lab_id=lab_id,
                    score=total_score,
                    feedback=", ".join(all_feedback),
                    duration=duration,
                    status="done",
                    timestamp=datetime.now(wib)
                )
                db_session.add(res)

        if grading_type == "kelompok":
            # Update semua anggota kelompok
            group_users = db_session.query(User).filter_by(class_name=user_info.class_name, group_name=user_info.group_name).all()
            for g_user in group_users:
                save_to_db(g_user.username)
        else:
            # Individu / Individu per kelompok
            save_to_db(username)

        db_session.commit()

        return jsonify({
            "score": total_score if total_score is not None else 0,
            "feedback": all_feedback if isinstance(all_feedback, list) else [],
            "log_path": lab_log_path,
            "duration": duration if duration is not None else 0,
            "penalty": penalty_messages
        }), 200

    except json.JSONDecodeError as e:
        print(f"JSON Decode Error: {str(e)}")
        return jsonify({"error": "Invalid JSON format", "details": str(e)}), 400
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route('/finish-lab', methods=['POST'])
def finish_lab():
    try:
        auth_header = request.headers.get("Authorization")
        token = auth_header.replace("Bearer ", "")

        # FIX: Jangan cuma ambil dari ACTIVE_LABS karena kalau restart server datanya ilang
        # Ambil username langsung dari token
        try:
            username = token.split("-")[-1]
        except:
            return jsonify({"error": "Token tidak valid"}), 401

        data = request.get_json()
        lab_id = data.get("lab_id")

        if not lab_id:
            return jsonify({"error": "lab_id dibutuhkan"}), 400

        # Jalankan cleanup
        #run_cleanup_actions(lab_id, username)

        # Bersihkan session jika ada
        if token in ACTIVE_LABS:
            del ACTIVE_LABS[token]

        return jsonify({"message": f"Lab {lab_id} selesai!"}), 200

        session = db_session.query(LabSession).filter_by(
            group_name=user.group_name, 
            lab_id=lab_id
        ).first()

        if not session:
            return jsonify({"error": "Gagal! Anda belum memulai (start) lab ini!"}), 400

    except Exception as e:
        print(f"Error in finish_lab: {str(e)}")
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route('/add-lab', methods=['POST'])
def add_lab():
    try:
        data = request.json
        lab_id = data.get("lab_id")
        scheme_path = data.get("scheme_path")

        if not lab_id or not scheme_path:
            return jsonify({"error": "lab_id dan scheme_path dibutuhkan"}), 400

        # Cek apakah lab sudah ada
        existing_lab = db_session.query(Lab).filter(Lab.lab_id == lab_id).first()
        if existing_lab:
            return jsonify({"error": "Lab sudah ada"}), 400

        # Buat lab baru
        new_lab = Lab(lab_id=lab_id, scheme_path=scheme_path)
        db_session.add(new_lab)
        db_session.commit()

        return jsonify({"message": "Lab berhasil ditambahkan!"}), 201

    except Exception as e:
        print(f"Error in add-lab: {str(e)}")  # Debugging
        db_session.rollback()
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route('/list-labs', methods=['GET'])
def list_labs():
    try:
        labs = db_session.query(Lab).all()
        lab_list = [{"lab_id": lab.lab_id, "scheme_path": lab.scheme_path} for lab in labs]
        return jsonify({"labs": lab_list}), 200
    except Exception as e:
        print(f"Error in list-labs: {str(e)}")  # Debugging
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route('/users-not-started-lab-filtered', methods=['GET'])
def users_not_started_lab_filtered():
    try:
        class_name = request.args.get("class_name")
        lab_id = request.args.get("lab_id")

        # Ambil semua user yang sudah memulai lab tertentu
        users_started_lab = db_session.query(GradingResult.username).filter(GradingResult.lab_id == lab_id).all()
        users_started_lab = [user[0] for user in users_started_lab]

        # Ambil semua user berdasarkan class_name (jika ada filter class_name)
        query = db_session.query(User)
        if class_name:
            query = query.filter(User.class_name == class_name)
        all_users = query.all()

        # Filter user yang belum memulai lab
        users_not_started = [user.username for user in all_users if user.username not in users_started_lab]

        return jsonify({
            "users_not_started": users_not_started,
            "class_name": class_name,
            "lab_id": lab_id
        }), 200
    except Exception as e:
        print(f"Error in users-not-started-lab-filtered: {str(e)}")
        return jsonify({"error": "Gagal mengambil daftar user yang belum start lab", "details": str(e)}), 500

@app.route('/users-not-started-lab', methods=['GET'])
def users_not_started_lab():
    try:
        lab_id = request.args.get("lab_id")

        if not lab_id:
            return jsonify({"error": "lab_id dibutuhkan"}), 400

        # Ambil semua user yang belum memulai lab ini
        users_started_lab = db_session.query(GradingResult.username).filter(GradingResult.lab_id == lab_id).all()
        users_started_lab = [user[0] for user in users_started_lab]

        # Ambil semua user yang belum memulai lab
        all_users = db_session.query(User.username).all()
        all_users = [user[0] for user in all_users]

        users_not_started = list(set(all_users) - set(users_started_lab))

        return jsonify({"users_not_started": users_not_started}), 200

    except Exception as e:
        print(f"Error in users-not-started-lab: {str(e)}")  # Debugging
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route('/get-users-and-labs', methods=['GET'])
def get_users_and_labs():
    try:
        # Ambil semua user dari tabel users
        users = db_session.query(User).all()
        user_list = [
            {
                "username": user.username,
                "class_name": user.class_name,
                "name": user.name,
                "group_name": user.group_name
            }
            for user in users
        ]

        # Ambil semua lab dari tabel labs
        labs = db_session.query(Lab).all()
        lab_list = [{"lab_id": lab.lab_id} for lab in labs]

        # Ambil semua user yang sudah memulai lab dari tabel grading_results
        users_started_lab = db_session.query(GradingResult.username).distinct().all()
        users_started_lab = [user[0] for user in users_started_lab]

        return jsonify({
            "users": user_list,
            "labs": lab_list,
            "users_started_lab": users_started_lab
        }), 200
    except Exception as e:
        print(f"Error fetching users and labs: {str(e)}")
        return jsonify({"error": "Gagal mengambil data users dan labs", "details": str(e)}), 500

@app.route('/results', methods=['GET'])
def show_results():
    if not session.get('logged_in'):
        return redirect(url_for('login_web'))

    try:
        # Ambil parameter dari URL
        class_name = request.args.get("class_name")
        group_name = request.args.get("group_name")
        lab_id = request.args.get("lab_id")
        search_name = request.args.get("search_name")
        page = int(request.args.get("page", 1))
        per_page = 10

        # Ambil data referensi untuk Dropdown
        all_classes = db_session.query(Class).all()
        all_groups = db_session.query(Group).all()
        all_labs = db_session.query(Lab).all()
        all_users = db_session.query(User).order_by(User.name).all()

        # Query Utama Results
        query = (
            db_session.query(GradingResult)
            .join(User, User.username == GradingResult.username)
        )

        # LOGIKA FILTER
        if class_name:
            query = query.filter(GradingResult.class_name == class_name)
        if group_name:
            query = query.filter(User.group_name == group_name)
        if lab_id:
            query = query.filter(GradingResult.lab_id == lab_id)
        if search_name:
            query = query.filter(User.name == search_name)

        total_results = query.count()
        results = query.offset((page - 1) * per_page).limit(per_page).all()

        # Pembersihan data - INI YANG HARUS DI-INDENT BENAR!
        for result in results:
            result.local_timestamp = result.timestamp if result.timestamp else '-'
            user_info = db_session.query(User).filter(User.username == result.username).first()
            if user_info:
                result.display_name = user_info.name
                result.display_group = user_info.group_name or 'N/A'
                result.display_class = getattr(user_info, 'class_name', result.class_name) or 'N/A'
            else:
                result.display_name = result.username
                result.display_group = 'N/A'
                result.display_class = result.class_name or 'N/A'

        return render_template(
            'results.html',
            results=results,
            classes=all_classes,
            groups=all_groups,
            labs=all_labs,
            user_list=all_users,
            class_name=class_name,  # Gunakan ini untuk selected di template
            group_name=group_name,
            lab_id=lab_id,
            search_name=search_name,
            page=page,
            total_pages=(total_results + per_page - 1) // per_page
        )
    except Exception as e:
        print("Error:", str(e))
        return jsonify({"error": "Gagal", "details": str(e)}), 500

@app.route('/download-results', methods=['GET'])
def download_results():
    try:
        class_name = request.args.get("class_name", "").strip()
        lab_id = request.args.get("lab_id", "").strip()
        search_name = request.args.get("search_name", "").strip()  # TAMBAH INI

        query = db_session.query(GradingResult)

        # Filter berdasarkan class_name
        if class_name:
            query = query.filter(GradingResult.class_name == class_name)

        # Filter berdasarkan lab_id
        if lab_id:
            query = query.filter(GradingResult.lab_id == lab_id)

        # TAMBAH: Filter berdasarkan user name (search_name)
        if search_name:
            # Join dengan User table untuk filter berdasarkan nama
            query = query.join(User, GradingResult.username == User.username)
            query = query.filter(User.name == search_name)

        results = query.order_by(
            GradingResult.username.asc(),
            GradingResult.lab_id.asc(),
            GradingResult.score.desc()).all()

        print(f"Number of results fetched: {len(results)}")

        users = db_session.query(User).all()

        best_results = {}
        for result in results:
            key = (result.username, result.class_name, result.lab_id)
            if key not in best_results or result.score > best_results[key].score:
                best_results[key] = result

        final_results = list(best_results.values())

        # Buat file CSV dalam memori
        output = StringIO()
        writer = csv.writer(output)

        # Tulis header CSV
        writer.writerow(['Username', 'Nama', 'Kelas', 'Kelompok', 'Lab ID', 'Score', 'Timestamp'])

        # Tulis data ke CSV
        for result in final_results:
            user = next((u for u in users if u.username == result.username), None)
            writer.writerow([
                result.username,
                user.name if user else '',
                result.class_name,
                user.group_name if user else '',
                result.lab_id,
                result.score,
             #   result.feedback,
                result.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            ])

        # Siapkan respons untuk mengunduh file CSV
        output.seek(0)
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = 'attachment; filename=grading_results.csv'
        response.headers['Content-type'] = 'text/csv'

        return response

    except Exception as e:
        print(f"Error generating CSV: {str(e)}")
        return jsonify({"error": "Gagal membuat file CSV", "details": str(e)}), 500

@app.route('/get-filters', methods=['GET'])
def get_filters():
    try:
        # Ambil daftar class_name yang unik
        class_names = db_session.query(GradingResult.class_name).distinct().all()
        class_names = [name[0] for name in class_names]

        # Ambil daftar lab_id yang unik
        lab_ids = db_session.query(GradingResult.lab_id).distinct().all()
        lab_ids = [lab_id[0] for lab_id in lab_ids]

        return jsonify({
            "class_names": class_names,
            "lab_ids": lab_ids
        }), 200
    except Exception as e:
        print(f"Error fetching filters: {str(e)}")
        return jsonify({"error": "Gagal mengambil filter", "details": str(e)}), 500

@app.route('/delete-result', methods=['POST'])
def delete_result():
    try:
        data = request.get_json()

        if not data or "id" not in data:
            return jsonify({"error": "Data permintaan tidak valid"}), 400

        result_id = data.get("id")

        # Cari data berdasarkan ID
        result = db_session.query(GradingResult).filter(GradingResult.id == result_id).first()
        if not result:
            return jsonify({"error": "Hasil tidak ditemukan"}), 404

        # Hapus data dari database
        db_session.delete(result)
        db_session.commit()

        return jsonify({"message": f"Hasil dengan ID {result_id} berhasil dihapus!"}), 200

    except Exception as e:
        print(f"Error deleting result: {str(e)}")  # Debugging
        db_session.rollback()
        return jsonify({"error": "Gagal menghapus hasil", "details": str(e)}), 500

@app.route('/scheme-editor', methods=['GET'])
def scheme_editor():
    try:
        # Render file scheme_editor.html
        return render_template('scheme_editor.html')
    except Exception as e:
        print(f"Error rendering scheme editor: {str(e)}")  # Debugging
        return jsonify({"error": "Gagal membuat editor skema", "details": str(e)}), 500

@app.route('/create_scheme', methods=['GET', 'POST'])
def create_scheme():
    types = ["command", "file_exists", "file_content", "service", "directory", "config_check", "package", "user", "group", "gitlab_pipeline", "gitlab_project", "gitlab_runner", "image", "instance", "grafana_alert_rule", "grafana_alert_firing", "gmail_alert_email" ]
    expected = {
        "command": ["true", "false"],
        "file_exists": ["exists", "deleted"],
        "file_content": ["contains"],
        "service": ["active", "inactive"],
        "directory": ["exists"],
        "config_check": ["correct"],
        "package": ["installed"],
        "user": ["exists", "deleted"],
        "group": ["exists", "deleted"],
        "gitlab_pipeline": ["success"],
        "gitlab_project": ["exists"],
        "gitlab_runner": ["exists"],
        "image": ["exists"],
        "grafana_alert_rule": ["exists"],
        "grafana_alert_firing": ["exists"],
        "gmail_alert_email": ["exists"],
        "instance": ["deleted"]
    }

    if request.method == "GET":
        return render_template("add_scheme.html", types=types, expected=expected)

    try:
        data = request.get_json()

        if not data or "lab_id" not in data or "criteria" not in data:
            return jsonify({"error": "Data permintaan tidak valid"}), 400

        lab_id = data.get("lab_id")
        criteria = data.get("criteria")
        grading_type = data.get("grading_type", "kelompok")

        # Validasi kriteria
        if not isinstance(criteria, list) or len(criteria) == 0:
            return jsonify({"error": "Minimal satu kriteris wajib ada"}), 400

        # Hitung skor otomatis
        num_criteria = len(criteria)
        score_per_criterion = 100 / num_criteria

        # Update skor untuk setiap kriteria
        for criterion in criteria:
            criterion["score"] = round(score_per_criterion, 2)

        # Buat skema baru
        scheme = {
            "lab_id": lab_id,
            "grading_type": grading_type,
            "criteria": criteria
        }

        # Simpan skema ke file
        scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")
        with open(scheme_file, 'w') as f:
            json.dump(scheme, f, indent=4)

        # Tambahkan lab ke database jika belum ada
        existing_lab = db_session.query(Lab).filter(Lab.lab_id == lab_id).first()
        if not existing_lab:
            new_lab = Lab(lab_id=lab_id, scheme_path=scheme_file, grading_type=grading_type)
            db_session.add(new_lab)
            db_session.commit()

        return jsonify({"message": f"Skema '{lab_id}' berhasil dibuat!"}), 200

    except Exception as e:
        print(f"Error creating scheme: {str(e)}")  # Debugging
        db_session.rollback()
        return jsonify({"error": "Gagal membuat skema", "details": str(e)}), 500

@app.route('/edit_scheme', methods=['POST'])
def edit_scheme():
    if not session.get('logged_in'):
        return redirect(url_for('login_web'))

    try:
        data = request.get_json()

        if not data or "lab_id" not in data or "criteria" not in data:
            return jsonify({"error": "Data permintaan tidak valid"}), 400

        lab_id = data.get("lab_id")
        criteria = data.get("criteria")
        grading_type = data.get("grading_type", "kelompok") # Tangkap tipe grading

        # Hitung skor otomatis
        num_criteria = len(criteria)
        if num_criteria == 0:
            return jsonify({"error": "Minimal satu kriteris wajib ada"}), 400

        score_per_criterion = 100 / num_criteria

        # Update skor untuk setiap kriteria
        for criterion in criteria:
            criterion["score"] = round(score_per_criterion, 2)

        # Buat skema baru
        scheme = {
            "lab_id": lab_id,
            "grading_type": grading_type,
            "criteria": criteria

        }

        # Simpan skema ke file
        scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")
        with open(scheme_file, 'w') as f:
            json.dump(scheme, f, indent=4)

        # Tambahkan lab ke database jika belum ada
        existing_lab = db_session.query(Lab).filter(Lab.lab_id == lab_id).first()
        if existing_lab:
            existing_lab.scheme_path = scheme_file
            existing_lab.grading_type = grading_type # Update tipe grading di DB
            db_session.commit()

        return jsonify({"message": f"Skema '{lab_id}' berhasil diupdate!"}), 200

    except Exception as e:
        print(f"Error editing scheme: {str(e)}")  # Debugging
        db_session.rollback()
        return jsonify({"error": "Gagal mengubah skema", "details": str(e)}), 500

@app.route('/edit_scheme/<lab_id>', methods=['GET'])
def edit_scheme_page(lab_id):
    if not session.get('logged_in'):
        return redirect(url_for('login_web'))

    scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")
    if not os.path.exists(scheme_file):
        return "Scheme not found", 404

    with open(scheme_file, "r") as f:
        scheme = json.load(f)

    types = ["command", "file_exists", "file_content", "service", "directory", "config_check", "package", "user", "group", "gitlab_pipeline", "gitlab_project", "gitlab_runner", "image", "grafana_alert_rule", "gmail_alert_email", "instance"]
    expected = {
        "command": ["true", "false"],
        "file_exists": ["exists", "deleted"],
        "file_content": ["contains"],
        "service": ["active", "inactive"],
        "directory": ["exists"],
        "config_check": ["correct"],
        "package": ["installed"],
        "user": ["exists", "deleted"],
        "group": ["exists", "deleted"],
        "gitlab_pipeline": ["success"],
        "gitlab_project": ["exists"],
        "gitlab_runner": ["exists"],
        "image": ["exists"],
        "grafana_alert_rule": ["exists"],
        "grafana_alert_firing": ["exists"],
        "gmail_alert_email": ["exists"],
        "instance": ["deleted"]
    }

    return render_template("edit_scheme.html", scheme=scheme, types=types, expected=expected)

@app.route('/edit_scheme/<lab_id>', methods=['POST'])
def edit_scheme_post(lab_id):
    if not session.get('logged_in'):
        return redirect(url_for('login_web'))

    try:
        data = request.get_json()
        criteria = data.get("criteria")
        grading_type = data.get("grading_type", "kelompok") # Tangkap tipe grading
        # Jika perlu, bisa compare lab_id dari data dan URL, tapi biasanya cukup dari URL
        scheme = {
            "lab_id": lab_id,
            "grading_type": grading_type,
            "criteria": criteria
        }
        scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")
        with open(scheme_file, 'w') as f:
            json.dump(scheme, f, indent=4)
        # --- Update database jika perlu ---
        existing_lab = db_session.query(Lab).filter(Lab.lab_id == lab_id).first()
        if existing_lab:
            existing_lab.scheme_path = scheme_file
            db_session.commit()
        return jsonify({"message": f"Skema '{lab_id}' berhasil diupdate!"})
    except Exception as e:
        db_session.rollback()
        return jsonify({"error": "Gagal mengubah skema", "details": str(e)}), 500

@app.route('/delete-lab', methods=['POST'])
def delete_lab():
    try:
        data = request.json
        lab_id = data.get("lab_id")

        if not lab_id:
            return jsonify({"error": "lab_id dibutuhkan"}), 400

        # 1. Cari lab berdasarkan ID
        lab = db_session.query(Lab).filter(Lab.lab_id == lab_id).first()
        if not lab:
            return jsonify({"error": "Lab tidak ditemukan"}), 404

        # 2. Hapus FILE FISIK-nya dulu sebelum datanya dihapus di DB
        # Kita ambil path file dari kolom scheme_path di database
        if lab.scheme_path and os.path.exists(lab.scheme_path):
            os.remove(lab.scheme_path)
            print(f"File {lab.scheme_path} berhasil dihapus.")

        # 3. Hapus data di database
        db_session.delete(lab)
        db_session.commit()

        return jsonify({"message": f"Lab '{lab_id}' dan file fisiknya berhasil dihapus!"}), 200

    except Exception as e:
        print(f"Error in delete-lab: {str(e)}")
        db_session.rollback()
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route('/list-schemes', methods=['GET'])
def list_schemes():
    try:
        schemes = []
        for filename in os.listdir(SCHEME_PATH):
            if filename.endswith(".json"):
                scheme_file = os.path.join(SCHEME_PATH, filename)
                with open(scheme_file, 'r') as f:
                    scheme = json.load(f)
                    schemes.append(scheme)

                    # Tambahkan lab ke database jika belum ada
                    lab_id = scheme.get("lab_id")
                    existing_lab = db_session.query(Lab).filter(Lab.lab_id == lab_id).first()
                    if not existing_lab:
                        new_lab = Lab(lab_id=lab_id, scheme_path=scheme_file)
                        db_session.add(new_lab)
                        db_session.commit()

        return jsonify({"schemes": schemes}), 200

    except Exception as e:
        print(f"Error listing schemes: {str(e)}")  # Debugging
        db_session.rollback()
        return jsonify({"error": "Gagal menampilkan daftar skema", "details": str(e)}), 500

@app.route('/')
def home():
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))
    return redirect(url_for('login_web'))

@app.route('/schemes')
def scheme_list():
    if not session.get('logged_in'):
        return redirect(url_for('login_web'))

    order_by = request.args.get('order_by', 'name_asc')

    schemes = []
    for filename in os.listdir(SCHEME_PATH):
        if filename.endswith(".json"):
            scheme_file = os.path.join(SCHEME_PATH, filename)
            with open(scheme_file, 'r') as f:
                scheme = json.load(f)

            scheme.setdefault('lab_id', filename.replace('.json', ''))
            schemes.append(scheme)

    if order_by == 'name_asc':
        schemes.sort(key=lambda s: s.get('lab_id', '').lower())
    elif order_by == 'name_desc':
        schemes.sort(key=lambda s: s.get('lab_id', '').lower(), reverse=True)

    return render_template('index.html', schemes=schemes, order_by=order_by)


@app.route('/add_scheme')
def add_scheme():
    if not session.get('logged_in'):
        return redirect(url_for('login_web'))

    types = ["command", "file_exists", "file_content", "service", "directory", "config_check", "package", "user", "group", "gitlab_pipeline", "gitlab_project", "gitlab_runner", "image", "instance", "grafana_alert_rule", "grafana_alert_firing", "gmail_alert_email" ]
    expected = {
        "command": ["true", "false"],
        "file_exists": ["exists", "deleted"],
        "file_content": ["contains"],
        "service": ["active", "inactive"],
        "directory": ["exists"],
        "config_check": ["correct"],
        "package": ["installed"],
        "user": ["exists", "deleted"],
        "group": ["exists", "deleted"],
        "gitlab_pipeline": ["success"],
        "gitlab_project": ["exists"],
        "gitlab_runner": ["exists"],
        "image": ["exists"],
        "grafana_alert_rule": ["exists"],
        "grafana_alert_firing": ["exists"],
        "gmail_alert_email": ["exists"],
        "instance": ["deleted"]
    }

    return render_template('add_scheme.html', types=types, expected=expected)

@app.route('/get-log', methods=['GET'])
def get_log():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    lab_id = request.args.get("lab_id")
    # Validasi token/lab jika perlu, misal: cek user, cek status lab
    log_path = f"/var/log/gradingctl/labs/{lab_id}.log"
    try:
        with open(log_path, "r") as f:
            content = f.read()
        return jsonify({"content": content}), 200
    except Exception as e:
        return jsonify({"error": f"Log not found: {str(e)}"}), 404

@app.route("/webhook/gitlab", methods=["POST"])
def gitlab_webhook():
    # 1. Validasi secret token
    if request.headers.get("X-Gitlab-Token") != GITLAB_SECRET:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.json

    # 2. Ambil data penting
    project_id = data["project"]["id"]
    pipeline = data["object_attributes"]

    pipeline_id = pipeline["id"]
    status = pipeline["status"]
    ref = pipeline["ref"]
    sha = pipeline["sha"]

    # 3. Simpan status (cache / DB)
    ACTIVE_PIPELINES[(project_id, ref)] = {
        "pipeline_id": pipeline_id,
        "status": status,
        "sha": sha
    }

    return jsonify({"message": "Pipeline recorded"}), 200

def migrate_groups():
    """Mengambil data kelompok unik dari tabel User dan memasukkannya ke tabel Group"""
    try:
        existing_groups = db_session.query(User.group_name).distinct().all()
        for (g_name,) in existing_groups:
            if g_name:
                exists = db_session.query(Group).filter_by(group_name=g_name).first()
                if not exists:
                    new_group = Group(group_name=g_name)
                    db_session.add(new_group)
        db_session.commit()
    except Exception as e:
        print(f"Migration error: {e}")
        db_session.rollback()

def migrate_classes():
    """Mengambil data kelas unik dari tabel User dan memasukkannya ke tabel Class"""
    try:
        # 1. Ambil semua class_name unik dari tabel User
        existing_classes = db_session.query(User.class_name).distinct().all()

        for (c_name,) in existing_classes:
            if c_name:
                # 2. Cek apakah kelas sudah ada di tabel Class agar tidak duplikat
                exists = db_session.query(Class).filter_by(class_name=c_name).first()

                if not exists:
                    # 3. Masukkan ke tabel Class jika belum ada
                    new_class = Class(class_name=c_name)
                    db_session.add(new_class)
                    print(f"Migrating class: {c_name}")

        db_session.commit()
        print("Migration Class selesai!")
    except Exception as e:
        print(f"Migration error: {e}")
        db_session.rollback()

@app.route('/admin', methods=['GET'])
def admin_page():
    if not session.get('logged_in'):
        return redirect(url_for('login_web'))

    try:
        # Jalankan migrasi agar data dari tabel User masuk ke tabel Class & Group
        migrate_groups()
        migrate_classes()

        # --- LOGIKA PAGINATION ---
        page = request.args.get('page', 1, type=int)
        per_page = 10
        offset = (page - 1) * per_page

        # Ambil filter dari URL
        class_filter = request.args.get('class_name', '')
        group_filter = request.args.get('group_name', '')

        # Query dasar untuk User
        query = db_session.query(User)
        if class_filter:
            query = query.filter(User.class_name == class_filter)
        if group_filter:
            query = query.filter(User.group_name == group_filter)

        # Hitung total data & total halaman
        total_users = query.count()
        total_pages = (total_users + per_page - 1) // per_page

        # Ambil data user dengan limit 10 per halaman
        users = query.offset(offset).limit(per_page).all()

        # Ambil data untuk tabel bawah (Kelas & Kelompok) dan Dropdown
        all_classes = db_session.query(Class).order_by(Class.class_name.asc()).all()
        all_groups = db_session.query(Group).order_by(Group.group_name.asc()).all()
        all_admins = []
        if session.get('role') == 'superadmin':
            all_admins = db_session.query(Admin).all()

        return render_template('admin.html',
                               users=users,
                               groups=all_groups,
                               class_list=all_classes, # Untuk tabel data kelas
                               classes=all_classes,    # Untuk dropdown filter
                               selected_class=class_filter,
                               selected_group=group_filter,
                               all_admins=all_admins,
                               page=page,
                               total_pages=total_pages)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/add_group_db', methods=['POST'])
def add_group_db():
    data = request.json
    name = data.get("group_name")
    if not name: return jsonify({"error": "Nama kosong"}), 400
    if db_session.query(Group).filter_by(group_name=name).first():
        return jsonify({"error": "Kelompok sudah ada"}), 400
    db_session.add(Group(group_name=name))
    db_session.commit()
    return jsonify({"message": f"Kelompok {name} berhasil ditambahkan"})

@app.route('/get_groups', methods=['GET'])
def get_groups():
    try:
        # Mengambil semua kelompok dari tabel Group, urut abjad
        all_groups = db_session.query(Group).order_by(Group.group_name.asc()).all()
        group_list = [g.group_name for g in all_groups]
        return jsonify({"groups": group_list}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/delete_group', methods=['POST'])
def delete_group():
    data = request.json
    name = data.get("group_name")
    group = db_session.query(Group).filter_by(group_name=name).first()
    if group:
        db_session.delete(group)
        db_session.commit()
        return jsonify({"message": "Kelompok dihapus"})
    return jsonify({"error": "Gagal menghapus"}), 404

@app.route('/delete_user', methods=['POST'])
def delete_user():
    try:
        data = request.json
        username = data.get("username")
        if not username:
            return jsonify({"error": "Username dibutuhkan"}), 400

        user = db_session.query(User).filter(User.username == username).first()
        if not user:
            return jsonify({"error": "User tidak ditemukan"}), 404

        db_session.delete(user)
        db_session.commit()
        return jsonify({"message": f"User {username} berhasil dihapus"}), 200
    except Exception as e:
        db_session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/add_class_db', methods=['POST'])
def add_class_db():
    data = request.json
    new_class = Class(class_name=data.get('class_name'))
    db_session.add(new_class)
    db_session.commit()
    return jsonify({"message": "Berhasil"}), 200

@app.route('/delete_class', methods=['POST'])
def delete_class():
    data = request.json
    name = data.get('class_name')
    db_session.query(Class).filter(Class.class_name == name).delete()
    db_session.commit()
    return jsonify({"message": "Dihapus"}), 200

@app.route('/get_classes', methods=['GET'])
def get_classes():
    try:
        # Mengambil semua kelas dari tabel Class, urut abjad
        all_classes = db_session.query(Class).order_by(Class.class_name.asc()).all()
        class_list = [c.class_name for c in all_classes]
        return jsonify({"classes": class_list}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login_web'))

    # Total users dan labs - GUNAKAN db_session yang sudah diimport
    total_users = db_session.query(User).count()
    total_labs = db_session.query(Lab).count()
    
    # Ambil semua classes
    classes = db_session.query(Class).all()
    
    # Hitung progress per kelas
    class_progress = {}
    for cls in classes:
        class_name = cls.class_name
        
        # Ambil semua group dalam kelas ini
        groups_in_class = db_session.query(User.group_name).filter(
            User.class_name == class_name
        ).distinct().all()
        
        # Ambil lab yang sudah dikerjakan minimal 1 group di kelas ini
        completed_labs = set()
        for (group_name,) in groups_in_class:
            if group_name:  # Skip jika group_name kosong
                # Cek LabSession (sudah mulai/dikerjakan)
                sessions = db_session.query(LabSession.lab_id).filter(
                    LabSession.group_name == group_name
                ).all()
                
                # Cek GradingResult (sudah dinilai) - query lebih efisien
                user_usernames = db_session.query(User.username).filter(
                    User.group_name == group_name,
                    User.class_name == class_name
                ).all()
                usernames = [u[0] for u in user_usernames]
                
                if usernames:
                    results = db_session.query(GradingResult.lab_id).filter(
                        GradingResult.username.in_(usernames)
                    ).distinct().all()
                else:
                    results = []
                
                # Gabungkan lab yang sudah dikerjakan/dinilai
                for (lab_id,) in sessions + results:
                    completed_labs.add(lab_id)
        
        # Hitung persentase
        progress_percent = (len(completed_labs) / total_labs * 100) if total_labs > 0 else 0
        class_progress[class_name] = {
            'completed': len(completed_labs),
            'total': total_labs,
            'percent': round(progress_percent, 1)
        }
    
    return render_template('dashboard.html',
                         total_users=total_users,
                         total_labs=total_labs,
                         classes=classes,
                         class_progress=class_progress)

@app.route('/auth', methods=['GET', 'POST'])
def login_web():
    if request.method == 'POST':
        try:
            data = request.get_json(silent=True) or {}
            username = data.get('username') or request.form.get('username')
            password = data.get('password') or request.form.get('password')

            # --- 1. CEK KE JSON (SUPERADMIN) ---
            static_users = load_static_users()
            if username in static_users and static_users[username] == password:
                session.permanent = True
                session['logged_in'] = True
                session['username'] = username
                session['role'] = 'superadmin' # Tandai sebagai superadmin
                
                return jsonify({"success": True, "redirect": url_for('dashboard')})

            # --- 2. CEK KE DATABASE (ADMIN BIASA) ---
            # Cari di tabel 'admins' yang kita buat tadi
            admin_db = db_session.query(Admin).filter_by(username=username, password=password).first()
            if admin_db:
                session.permanent = True
                session['logged_in'] = True
                session['username'] = admin_db.username
                session['role'] = 'admin' # Tandai sebagai admin biasa
                
                return jsonify({"success": True, "redirect": url_for('dashboard')})

            # Jika dua-duanya gagal
            error_msg = "Username atau Password Salah!"
            return jsonify({"success": False, "error": error_msg}), 401

        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # INI ADALAH BAGIAN GET
    # Jika method-nya GET, langsung tampilkan halaman login.html
    return render_template('login.html')

@app.route('/add_admin_db', methods=['POST'])
def add_admin():
    if session.get('role') != 'superadmin':
        return jsonify({"success": False, "error": "Unauthorized!"}), 403

    data = request.json
    username = data.get('username')
    password = data.get('password')
    email = data.get('email')

    if not all([username, password, email]):
        return jsonify({"success": False, "error": "Data tidak lengkap!"}), 400

    try:
        # Langsung simpan ke tabel Admin dengan email
        new_admin = Admin(username=username, password=password, email=email)
        db_session.add(new_admin)
        db_session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db_session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/delete_admin/<int:admin_id>', methods=['DELETE'])
def delete_admin(admin_id):
    if session.get('role') != 'superadmin':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    admin = db_session.query(Admin).get(admin_id)
    if admin:
        db_session.delete(admin)
        db_session.commit()
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Admin not found"}), 404

# Ubah nama fungsi logout menjadi logout_web
@app.route('/logout')
def logout_web():
    session.clear()
    return redirect(url_for('login_web'))

# HAPUS fungsi reset_password() yang lama di api.py
# Ganti dengan dua fungsi ini:

otp_storage = {}

@app.route('/request_reset_otp', methods=['POST'])
def request_reset_otp():
    data = request.json
    username = data.get("username")
    email_input = data.get("email")

    # 1. Cari di tabel User dulu
    account = db_session.query(User).filter_by(username=username).first()
    
    # 2. Kalau tidak ada di User, cari di tabel Admin
    if not account:
        account = db_session.query(Admin).filter_by(username=username).first()

    if not account:
        return jsonify({"error": "Akun tidak ditemukan"}), 404

    if account.email != email_input:
        return jsonify({"error": "Email tidak cocok"}), 400

    # Generate OTP
    otp = str(random.randint(100000, 999999))
    expired = datetime.now() + timedelta(minutes=20)

    # Simpan ke akun yang ditemukan (bisa User atau Admin)
    account.otp_code = otp
    account.otp_expiry = expired
    db_session.commit()

    if send_otp_gmail(account.email, otp):
        return jsonify({"message": "OTP berhasil dikirim ke email!"}), 200
    else:
        return jsonify({"error": "Gagal mengirim email OTP"}), 500

@app.route('/reset_password_with_otp', methods=['POST'])
def reset_password_with_otp():
    data = request.json
    username = data.get("username")
    otp = data.get("otp")
    new_password = data.get("new_password")

    # Cari di User atau Admin
    account = db_session.query(User).filter_by(username=username).first()
    if not account:
        account = db_session.query(Admin).filter_by(username=username).first()

    if not account:
        return jsonify({"error": "Akun tidak ditemukan"}), 404

    # Validasi OTP
    if account.otp_code != otp:
        return jsonify({"error": "Kode OTP salah"}), 400

    if account.otp_expiry < datetime.now():
        return jsonify({"error": "OTP sudah kadaluarsa"}), 400

    # Update Password & Hapus OTP
    account.password = new_password
    account.otp_code = None
    account.otp_expiry = None
    db_session.commit()

    return jsonify({"message": "Password berhasil direset"}), 200

@app.route('/request_reset_link', methods=['POST'])
def request_reset_link():
    try:
        data = request.json
        username = data.get('username')
        email = data.get('email', '').strip().lower()

        user = db_session.query(User).filter_by(
            username=username,
            email=email
        ).first()

        if not user:
            return jsonify({"error": "Username atau Email tidak cocok!"}), 404

        # TOKEN SAJA (TANPA EXPIRE)
        token = str(uuid.uuid4())
        user.reset_token = token
        db_session.commit()

        reset_url = f"https://grading.smkn1cibinong.sch.id/reset-page/{token}"

        smtp_user = "monitoringsija@gmail.com"
        smtp_pass = "seggglrmhfquoobr"

        msg = MIMEMultipart()
        msg['From'] = f"Monitoring SIJA <{smtp_user}>"
        msg['To'] = email
        msg['Subject'] = "Reset Password Akun Grading CTL"

        body = f"""Halo {username},

Klik link berikut untuk reset password Anda:
{reset_url}
"""
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=15)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        server.quit()

        return jsonify({"message": "Link reset password telah dikirim ke email."}), 200

    except Exception as e:
        db_session.rollback()
        print(f"Error Reset Password: {e}")
        return jsonify({"error": "Gagal mengirim reset password."}), 500


@app.route('/reset-page/<token>', methods=['GET', 'POST'])
def reset_page(token):
    print("\n--- DEBUG START ---")
    print(f"Token dari URL: {token}")

    user = db_session.query(User).filter_by(reset_token=token).first()

    if not user:
        print("Token tidak ditemukan")
        print("--- DEBUG END ---\n")
        return "<h1>Link Tidak Valid</h1><p>Token reset password tidak ditemukan.</p>", 400

    print(f"Token valid untuk user: {user.username}")
    print("--- DEBUG END ---\n")

    if request.method == 'POST':
        new_pw = request.form.get('password')

        if not new_pw:
            return "Password tidak boleh kosong", 400

        user.password = new_pw
        user.reset_token = None   # token dipakai sekali
        db_session.commit()

        return "OK", 200

    return render_template(
        'reset_password.html',
        username=user.username
    )

@app.route('/forgot-password')
def forgot_password_page():
    return render_template('forgot_password.html')

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
