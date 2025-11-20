from flask import Flask, request, jsonify, render_template, make_response 
import os
import json
from database import db_session, init_db
from models import User, Lab, GradingResult
from datetime import datetime
import pytz
wib = pytz.timezone("Asia/Jakarta")
timestamp = datetime.now(wib)
import csv
from io import StringIO

app = Flask(__name__)

SCHEME_PATH = "/opt/grading/app/schemes/"
ACTIVE_LABS = {}

@app.before_request
def validate_content_type():
    if request.method == 'POST' and not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

@app.route('/register', methods=['POST'])
def register():
    try:
        data = request.json
        username = data.get("username")
        password = data.get("password")
        class_name = data.get("class_name")

        # Validasi input
        if not username or not password or not class_name:
            return jsonify({"error": "Username, password, and class_name are required"}), 400

        # Cek apakah username sudah ada
        existing_user = db_session.query(User).filter(User.username == username).first()
        if existing_user:
            return jsonify({"error": "Username already exists"}), 400

        # Buat user baru
        new_user = User(username=username, password=password, class_name=class_name)
        db_session.add(new_user)
        db_session.commit()

        return jsonify({"message": "User registered successfully"}), 201

    except Exception as e:
        print(f"Error in register: {str(e)}")  # Debugging
        db_session.rollback()
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.json
        username = data.get("username")
        password = data.get("password")

        # Validasi input
        if not username or not password:
            return jsonify({"error": "Username and password are required"}), 400

        # Cari user di database
        user = db_session.query(User).filter(User.username == username).first()
        if not user:
            return jsonify({"error": "User not exist, please register first (gradingctl register)"}), 404

        # Validasi password
        if user.password != password:
            return jsonify({"error": "Invalid password"}), 401

        # Buat token (contoh sederhana)
        token = f"dummy-token-{username}-{user.class_name}"
        return jsonify({"token": token, "class_name": user.class_name}), 200

    except Exception as e:
        print(f"Error in login: {str(e)}")  # Debugging
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route('/start-lab', methods=['POST'])
def start_lab():
    try:
        # Ambil token dari header Authorization
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"error": "Invalid or missing Authorization header"}), 401

        token = auth_header.split("Bearer ")[-1]
        if not token:
            return jsonify({"error": "Token is missing"}), 401

        # Ambil lab_id dari body request
        data = request.get_json()
        if not data or "lab_id" not in data:
            return jsonify({"error": "Invalid request data"}), 400

        lab_id = data.get("lab_id")
        scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")

        # Periksa apakah file skema lab ada
        if not os.path.exists(scheme_file):
            return jsonify({"error": f"Lab '{lab_id}' not found"}), 404

        from datetime import datetime
        import pytz
        wib = pytz.timezone("Asia/Jakarta")
        start_time = datetime.now(wib)

        # Simpan lab yang aktif untuk token ini
        ACTIVE_LABS[token] = {"lab_id": lab_id, "status": "active", "start_time": start_time}

        return jsonify({"message": f"Lab '{lab_id}' started successfully"}), 200

    except Exception as e:
        print(f"Error in start_lab: {str(e)}")  # Debugging
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route('/get-scheme-description', methods=['GET'])
def get_scheme_description():
    lab_id = request.args.get("lab_id")
    scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")

    if not os.path.exists(scheme_file):
        return jsonify({"error": "Lab not found"}), 404

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
        return jsonify({"error": "Lab not found"}), 404

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
        print("FULL DATA POSTED:", data)

        if not data:
            return jsonify({"error": "Invalid request data"}), 400

        lab_id = data.get("lab_id")
        class_name = data.get("class_name")  # Ambil class_name dari payload
        client_data = data.get("client_data", {})

        lab_log_path = f"/var/log/nusactl/labs/{lab_id}.log"
        os.makedirs(os.path.dirname(lab_log_path), exist_ok=True)

        if not lab_id or not class_name:  # Pastikan lab_id dan class_name ada
            return jsonify({"error": "lab_id and class_name are required"}), 400

        # Ambil username dari token (format: dummy-token-username-class)
        username = token.split("-")[2]  # Ambil bagian ketiga setelah split

        # Debugging: Cetak username dan class_name
        print(f"Username: {username}, Class Name: {class_name}")

        if not isinstance(client_data, dict):
            return jsonify({"error": "Invalid client data format"}), 400

        active_lab = ACTIVE_LABS.get(token)
        if not active_lab or active_lab["lab_id"] != lab_id:
            return jsonify({"error": "Lab not started or invalid lab"}), 403

        scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")
        if not os.path.exists(scheme_file):
            return jsonify({"error": "Lab not found"}), 404

        with open(scheme_file, 'r') as f:
            scheme = json.load(f)

        total_score = 0
        feedback = []

        # Proses setiap kriteria dalam skema
        for criterion in scheme.get("criteria", []):
            ctype = criterion.get("type")
            key = criterion.get("key")
            expected = criterion.get("expected")
            description = criterion.get("description")
            score = criterion.get("score", 0)

            actual_value = client_data.get(key, "")

            # Logika evaluasi kriteria
            if ctype == "command":
                if expected and actual_value and expected.lower() in actual_value.lower():
                    total_score += score
                else:
                    feedback.append(f"{description}: Failed")
                    with open(lab_log_path, 'a') as logfile:
                        logfile.write(f"[{datetime.now(wib)}] CASE: {description} | ERROR: {actual_value}\n")
            elif ctype == "file_exists":
                if expected == "deleted" and actual_value == "deleted":
                    total_score += score
                elif expected == "exists" and actual_value == "exists":
                    total_score += score
                else:
                    feedback.append(f"{description}: Failed")
                    with open(lab_log_path, 'a') as logfile:
                        logfile.write(f"[{datetime.now(wib)}] CASE: {description} | ERROR: {actual_value}\n")
            elif ctype == "file_content":
                contains = criterion.get("contains")
                if contains and contains in actual_value:
                    total_score += score
                else:
                    feedback.append(f"{description}: Failed")
                    with open(lab_log_path, 'a') as logfile:
                        logfile.write(f"[{datetime.now(wib)}] CASE: {description} | ERROR: {actual_value}\n")
            elif ctype == "service":
                if expected == "active" and actual_value == "active":
                    total_score += score
                else:
                    feedback.append(f"{description}: Failed")
                    with open(lab_log_path, 'a') as logfile:
                        logfile.write(f"[{datetime.now(wib)}] CASE: {description} | ERROR: {actual_value}\n")
            elif ctype == "directory":
                if expected == "exists" and actual_value == "exists":
                    total_score += score
                else:
                    feedback.append(f"{description}: Failed")
                    with open(lab_log_path, 'a') as logfile:
                        logfile.write(f"[{datetime.now(wib)}] CASE: {description} | ERROR: {actual_value}\n")
            elif ctype == "config_check":
                if expected and actual_value == "correct":
                    total_score += score
                else:
                    feedback.append(f"{description}: Failed")
                    with open(lab_log_path, 'a') as logfile:
                        logfile.write(f"[{datetime.now(wib)}] CASE: {description} | ERROR: {actual_value}\n")
            elif ctype == "package":
                if expected == "installed" and actual_value == "installed":
                    total_score += score
                else:
                    feedback.append(f"{description}: Failed")
                    with open(lab_log_path, 'a') as logfile:
                        logfile.write(f"[{datetime.now(wib)}] CASE: {description} | ERROR: {actual_value}\n")
            elif ctype == "user":
                if expected == "exists" and actual_value == "exists":
                    total_score += score
                else:
                    feedback.append(f"{description}: Failed")
                    with open(lab_log_path, 'a') as logfile:
                        logfile.write(f"[{datetime.now(wib)}] CASE: {description} | ERROR: {actual_value}\n")
            elif ctype == "group":
                if expected == "exists" and actual_value == "exists":
                    total_score += score
                else:
                    feedback.append(f"{description}: Failed")
                    with open(lab_log_path, 'a') as logfile:
                        logfile.write(f"[{datetime.now(wib)}] CASE: {description} | ERROR: {actual_value}\n")
            else:
                feedback.append(f"Unsupported criterion type: {ctype}")

        # Pastikan `total_score` dihitung dengan benar
        print(f"Total score calculated: {total_score}")  # Debugging

        # Cobalah untuk menyimpan hasil grading ke database
        try:
            start_time = active_lab.get("start_time")
            #from datetime import datetime
            #import pytz
            #wib = pytz.timezone("Asia/Jakarta")
            end_time = datetime.now(wib)
            duration = (end_time - start_time).total_seconds() if start_time else None

            # Rubrik: pengurangan skor kalau lewat 10 menit
            max_duration = 600  # 10 menit dalam detik
            penalty_point = 10
            penalty_messages = []

            if duration is not None and duration > max_duration:
                total_score -= penalty_point
                penalty_messages.append(f"Waktu pengerjaan lebih dari 10 menit, pengurangan {penalty_point} poin." )
                if total_score < 0:
                    total_score = 0

            grading_result = GradingResult(
                username=username,  # Hanya username
                class_name=class_name,  # class_name terpisah
                lab_id=lab_id,
                score=total_score,
                feedback=", ".join(feedback),
		duration=duration,
		status="done"
            )
            db_session.add(grading_result)
            db_session.commit()  # Commit transaksi
            print(f"Data saved to database: {grading_result.username}, {grading_result.class_name}, {grading_result.lab_id}, {grading_result.score}")  # Debugging
        except Exception as db_error:
            print(f"Database Error: {str(db_error)}")  # Log error, rollback database transaksi
            db_session.rollback()

            #print(f'[DEBUG] Feedback to client: {feedback} (type={type(feedback)})')
            #print("[DEBUG] Feedback list sebelum return:", feedback)
            #print("[DEBUG] Feedback AKHIR:", feedback, type(feedback))
        # Nilai score tetap dikembalikan meskipun ada error pada penyimpanan database
        #return jsonify({"score": total_score, "feedback": feedback, "log_path": lab_log_path, "duration": duration, "penalty": penalty_messages,}), 200
        return jsonify({
            "score": total_score if total_score is not None else 0,
            "feedback": feedback if isinstance(feedback, list) else [],
            "log_path": lab_log_path,
            "duration": duration if duration is not None else 0,
            "penalty": penalty_messages
        }), 200


    except json.JSONDecodeError as e:
        print(f"JSON Decode Error: {str(e)}")  # Debugging
        return jsonify({"error": "Invalid JSON format", "details": str(e)}), 400
    except Exception as e:
        print(f"Error: {str(e)}")  # Debugging
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route('/finish-lab', methods=['POST'])
def finish_lab():
    try:
        # Ambil token dari header Authorization
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"error": "Invalid or missing Authorization header"}), 401

        token = auth_header.split("Bearer ")[-1]
        if not token:
            return jsonify({"error": "Token is missing"}), 401

        # Ambil lab_id dari body request
        data = request.get_json()
        if not data or "lab_id" not in data:
            return jsonify({"error": "Invalid request data"}), 400

        lab_id = data.get("lab_id")

        # Hapus lab yang aktif untuk token ini
        if token in ACTIVE_LABS and ACTIVE_LABS[token]["lab_id"] == lab_id:
            del ACTIVE_LABS[token]
            return jsonify({"message": f"Lab '{lab_id}' finished successfully"}), 200
        else:
            return jsonify({"error": "Lab not found or not active"}), 404

    except Exception as e:
        print(f"Error in finish_lab: {str(e)}")  # Debugging
        return jsonify({"error": "Server error", "details": str(e)}), 500

@app.route('/add-lab', methods=['POST'])
def add_lab():
    try:
        data = request.json
        lab_id = data.get("lab_id")
        scheme_path = data.get("scheme_path")

        if not lab_id or not scheme_path:
            return jsonify({"error": "lab_id and scheme_path are required"}), 400

        # Cek apakah lab sudah ada
        existing_lab = db_session.query(Lab).filter(Lab.lab_id == lab_id).first()
        if existing_lab:
            return jsonify({"error": "Lab already exists"}), 400

        # Buat lab baru
        new_lab = Lab(lab_id=lab_id, scheme_path=scheme_path)
        db_session.add(new_lab)
        db_session.commit()

        return jsonify({"message": "Lab added successfully"}), 201

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

@app.route('/delete-lab', methods=['POST'])
def delete_lab():
    try:
        data = request.json
        lab_id = data.get("lab_id")

        if not lab_id:
            return jsonify({"error": "lab_id is required"}), 400

        # Cari lab berdasarkan ID
        lab = db_session.query(Lab).filter(Lab.lab_id == lab_id).first()
        if not lab:
            return jsonify({"error": "Lab not found"}), 404

        # Hapus lab
        db_session.delete(lab)
        db_session.commit()

        return jsonify({"message": f"Lab '{lab_id}' deleted successfully"}), 200

    except Exception as e:
        print(f"Error in delete-lab: {str(e)}")  # Debugging
        db_session.rollback()
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
        return jsonify({"error": "Failed to fetch users not started lab", "details": str(e)}), 500

@app.route('/users-not-started-lab', methods=['GET'])
def users_not_started_lab():
    try:
        lab_id = request.args.get("lab_id")

        if not lab_id:
            return jsonify({"error": "lab_id is required"}), 400

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
        user_list = [{"username": user.username, "class_name": user.class_name} for user in users]

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
        return jsonify({"error": "Failed to fetch users and labs", "details": str(e)}), 500

@app.route('/results', methods=['GET'])
def show_results():
    try:
        class_name = request.args.get("class_name")
        lab_id = request.args.get("lab_id")
        page = int(request.args.get("page", 1))
        per_page = 10

        # Ambil data dari tabel users dan labs
        users = db_session.query(User).all()
        labs = db_session.query(Lab).all()

        # Ambil data dari tabel grading_results dengan filter
        query = db_session.query(GradingResult)

        if class_name:
            query = query.filter(GradingResult.class_name == class_name)
        if lab_id:
            query = query.filter(GradingResult.lab_id == lab_id)

        total_results = query.count()
        results = query.offset((page - 1) * per_page).limit(per_page).all()

        from datetime import timedelta
        for result in results:
            if result.timestamp:
                result.local_timestamp = result.timestamp + timedelta(hours=7)
            else:
                result.local_timestamp = '-'

        # Ambil user yang belum memulai lab dengan filter
        users_started_lab = db_session.query(GradingResult.username).distinct().all()
        users_started_lab = [user[0] for user in users_started_lab]
        users_not_started_lab = [user.username for user in users if user.username not in users_started_lab]

        return render_template(
            'results.html',
            results=results,
            users=users,
            labs=labs,
            users_not_started_lab=users_not_started_lab,
            class_name=class_name,
            lab_id=lab_id,
            page=page,
            total_pages=(total_results + per_page - 1) // per_page
        )
    except Exception as e:
        print("Error:", str(e))
        return jsonify({"error": "Failed to fetch results", "details": str(e)}), 500

@app.route('/download-results', methods=['GET'])
def download_results():
    try:
        class_name = request.args.get("class_name", "").strip()  # Ambil class_name, default ke string kosong
        lab_id = request.args.get("lab_id", "").strip()  # Ambil lab_id, default ke string kosong

        query = db_session.query(GradingResult)

        # Terapkan filter hanya jika class_name atau lab_id tidak kosong
        if class_name:
            query = query.filter(GradingResult.class_name == class_name)
        if lab_id:
            query = query.filter(GradingResult.lab_id == lab_id)

        results = query.all()

        # Debugging: Cetak jumlah hasil query
        print(f"Number of results fetched: {len(results)}")

        # Buat file CSV dalam memori
        output = StringIO()
        writer = csv.writer(output)

        # Tulis header CSV
        writer.writerow(['Username', 'Class Name', 'Lab ID', 'Score', 'Feedback', 'Timestamp'])

        # Tulis data ke CSV
        for result in results:
            writer.writerow([
                result.username,
                result.class_name,
                result.lab_id,
                result.score,
                result.feedback,
                result.timestamp.strftime('%Y-%m-%d %H:%M:%S')  # Format timestamp
            ])

        # Siapkan respons untuk mengunduh file CSV
        output.seek(0)
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = 'attachment; filename=grading_results.csv'
        response.headers['Content-type'] = 'text/csv'

        return response

    except Exception as e:
        print(f"Error generating CSV: {str(e)}")  # Debugging
        return jsonify({"error": "Failed to generate CSV", "details": str(e)}), 500

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
        return jsonify({"error": "Failed to fetch filters", "details": str(e)}), 500

@app.route('/delete-result', methods=['POST'])
def delete_result():
    try:
        data = request.get_json()

        if not data or "id" not in data:
            return jsonify({"error": "Invalid request data"}), 400

        result_id = data.get("id")

        # Cari data berdasarkan ID
        result = db_session.query(GradingResult).filter(GradingResult.id == result_id).first()
        if not result:
            return jsonify({"error": "Result not found"}), 404

        # Hapus data dari database
        db_session.delete(result)
        db_session.commit()

        return jsonify({"message": f"Result with ID {result_id} deleted successfully"}), 200

    except Exception as e:
        print(f"Error deleting result: {str(e)}")  # Debugging
        db_session.rollback()
        return jsonify({"error": "Failed to delete result", "details": str(e)}), 500

@app.route('/scheme-editor', methods=['GET'])
def scheme_editor():
    try:
        # Render file scheme_editor.html
        return render_template('scheme_editor.html')
    except Exception as e:
        print(f"Error rendering scheme editor: {str(e)}")  # Debugging
        return jsonify({"error": "Failed to load scheme editor", "details": str(e)}), 500

@app.route('/create_scheme', methods=['GET', 'POST'])
def create_scheme():
    types = ["command", "file_exists", "file_content", "service", "directory", "config_check", "package", "user", "group"]
    expected = {
        "command": ["true", "false"],
        "file_exists": ["exists", "deleted"],
        "file_content": ["contains"],
        "service": ["active", "inactive"],
        "directory": ["exists"],
        "config_check": ["correct"],
        "package": ["installed"],
        "user": ["exists", "deleted"],
        "group": ["exists", "deleted"]
    }

    if request.method == "GET":
        return render_template("add_scheme.html", types=types, expected=expected)

    try:
        data = request.get_json()

        if not data or "lab_id" not in data or "criteria" not in data:
            return jsonify({"error": "Invalid request data"}), 400

        lab_id = data.get("lab_id")
        criteria = data.get("criteria")

        # Validasi kriteria
        if not isinstance(criteria, list) or len(criteria) == 0:
            return jsonify({"error": "At least one criterion is required"}), 400

        # Hitung skor otomatis
        num_criteria = len(criteria)
        score_per_criterion = 100 / num_criteria

        # Update skor untuk setiap kriteria
        for criterion in criteria:
            criterion["score"] = round(score_per_criterion, 2)

        # Buat skema baru
        scheme = {
            "lab_id": lab_id,
            "criteria": criteria
        }

        # Simpan skema ke file
        scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")
        with open(scheme_file, 'w') as f:
            json.dump(scheme, f, indent=4)

        # Tambahkan lab ke database jika belum ada
        existing_lab = db_session.query(Lab).filter(Lab.lab_id == lab_id).first()
        if not existing_lab:
            new_lab = Lab(lab_id=lab_id, scheme_path=scheme_file)
            db_session.add(new_lab)
            db_session.commit()

        return jsonify({"message": f"Scheme '{lab_id}' created successfully"}), 200

    except Exception as e:
        print(f"Error creating scheme: {str(e)}")  # Debugging
        db_session.rollback()
        return jsonify({"error": "Failed to create scheme", "details": str(e)}), 500

@app.route('/edit_scheme', methods=['POST'])
def edit_scheme():
    try:
        data = request.get_json()

        if not data or "lab_id" not in data or "criteria" not in data:
            return jsonify({"error": "Invalid request data"}), 400

        lab_id = data.get("lab_id")
        criteria = data.get("criteria")

        # Hitung skor otomatis
        num_criteria = len(criteria)
        if num_criteria == 0:
            return jsonify({"error": "At least one criterion is required"}), 400

        score_per_criterion = 100 / num_criteria

        # Update skor untuk setiap kriteria
        for criterion in criteria:
            criterion["score"] = round(score_per_criterion, 2)

        # Buat skema baru
        scheme = {
            "lab_id": lab_id,
            "criteria": criteria
        }

        # Simpan skema ke file
        scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")
        with open(scheme_file, 'w') as f:
            json.dump(scheme, f, indent=4)

        # Tambahkan lab ke database jika belum ada
        existing_lab = db_session.query(Lab).filter(Lab.lab_id == lab_id).first()
        if not existing_lab:
            new_lab = Lab(lab_id=lab_id, scheme_path=scheme_file)
            db_session.add(new_lab)
            db_session.commit()

        return jsonify({"message": f"Scheme '{lab_id}' updated successfully"}), 200

    except Exception as e:
        print(f"Error editing scheme: {str(e)}")  # Debugging
        db_session.rollback()
        return jsonify({"error": "Failed to edit scheme", "details": str(e)}), 500

@app.route('/edit_scheme/<lab_id>', methods=['GET'])
def edit_scheme_page(lab_id):
    scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")
    if not os.path.exists(scheme_file):
        return "Scheme not found", 404

    with open(scheme_file, "r") as f:
        scheme = json.load(f)

    types = ["command", "file_exists", "file_content", "service", "directory", "config_check", "package", "user", "group"]

    expected = {
        "command": ["true", "false"],
        "file_exists": ["exists", "deleted"],
        "file_content": ["contains"],
        "service": ["active", "inactive"],
        "directory": ["exists"],
        "config_check": ["correct"],
        "package": ["installed"],
        "user": ["exists", "deleted"],
        "group": ["exists", "deleted"]
    }

    return render_template("edit_scheme.html", scheme=scheme, types=types, expected=expected)


@app.route('/delete_scheme', methods=['POST'])
def delete_scheme():
    try:
        data = request.get_json()

        if not data or "lab_id" not in data:
            return jsonify({"error": "Invalid request data"}), 400

        lab_id = data.get("lab_id")
        scheme_file = os.path.join(SCHEME_PATH, f"{lab_id}.json")

        if not os.path.exists(scheme_file):
            return jsonify({"error": f"Scheme '{lab_id}' not found"}), 404

        # Hapus file skema
        os.remove(scheme_file)

        # Hapus lab dari database
        lab = db_session.query(Lab).filter(Lab.lab_id == lab_id).first()
        if lab:
            db_session.delete(lab)
            db_session.commit()

        return jsonify({"message": f"Scheme '{lab_id}' deleted successfully"}), 200

    except Exception as e:
        print(f"Error deleting scheme: {str(e)}")  # Debugging
        db_session.rollback()
        return jsonify({"error": "Failed to delete scheme", "details": str(e)}), 500

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
        return jsonify({"error": "Failed to list schemes", "details": str(e)}), 500

@app.route('/')
def home():
    schemes = []
    for filename in os.listdir(SCHEME_PATH):
        if filename.endswith(".json"):
            scheme_file = os.path.join(SCHEME_PATH, filename)
            with open(scheme_file, 'r') as f:
                scheme = json.load(f)
                schemes.append(scheme)
    return render_template('index.html', schemes=schemes)

@app.route('/add_scheme')
def add_scheme():
    types = ["command", "file_exists", "file_content", "service", "directory", "config_check", "package", "user", "group"]
    expected = {
        "command": ["true", "false"],
        "file_exists": ["exists", "deleted"],
        "file_content": ["contains"],
        "service": ["active", "inactive"],
        "directory": ["exists"],
        "config_check": ["correct"],
        "package": ["installed"],
        "user": ["exists", "deleted"],
        "group": ["exists", "deleted"]
    }

    return render_template('add_scheme.html', types=types, expected=expected)


#@app.route('/add_scheme')
#def add_scheme():
#    expected = {"command": []}
#    return render_template('add_scheme.html', types=types, expected=expected)

#@app.route('/edit_scheme')
#def add_scheme():
#    expected = {"command": []}
#    return render_template('edit_scheme.html', expected=expected)

#@app.route('/add_scheme')
#def add_scheme():
#    expected = {
#        'command': []
#    }
#    return render_template('add_scheme.html')

@app.route('/get-log', methods=['GET'])
def get_log():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    lab_id = request.args.get("lab_id")
    # Validasi token/lab jika perlu, misal: cek user, cek status lab
    log_path = f"/var/log/nusactl/labs/{lab_id}.log"
    try:
        with open(log_path, "r") as f:
            content = f.read()
        return jsonify({"content": content}), 200
    except Exception as e:
        return jsonify({"error": f"Log not found: {str(e)}"}), 404

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
