from flask import Flask, render_template, request, redirect, session, url_for, jsonify, send_from_directory
import os
import json
from dotenv import load_dotenv
from datetime import datetime
from werkzeug.utils import secure_filename
import firebase_admin
from firebase_admin import credentials, firestore
import cloudinary
import cloudinary.uploader
import cloudinary.api
import math

# Konfiguracja ścieżek i folderów
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # Katalog gdzie jest app.py
DATA_DIR = os.path.join(BASE_DIR, 'data')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads', 'solutions')  # Zawsze w folderze projektu

# Tworzenie folderów
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Debug info przy starcie
print(f"=== FLASK APP DEBUG INFO ===")
print(f"BASE_DIR (katalog aplikacji): {BASE_DIR}")
print(f"DATA_DIR: {DATA_DIR}")
print(f"UPLOAD_FOLDER: {UPLOAD_FOLDER}")
print(f"Current working directory: {os.getcwd()}")
print(f"Upload folder exists: {os.path.exists(UPLOAD_FOLDER)}")
print("==============================")

# Dozwolone rozszerzenia plików
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'raw'}

load_dotenv()

def init_firebase():
    if firebase_admin._apps:
        return firestore.client()

    # Opcja 1: zmienna środowiskowa (produkcja na Render)
    firebase_json = os.getenv("FIREBASE_CREDENTIALS")
    if firebase_json:
        try:
            cred_dict = json.loads(firebase_json)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            print("Firebase: zainicjalizowano ze zmiennej środowiskowej")
            return firestore.client()
        except Exception as e:
            print(f"Firebase: błąd inicjalizacji ze zmiennej: {e}")

    # Opcja 2: lokalny plik firebase_key.json (development)
    key_path = os.path.join(BASE_DIR, 'firebase_key.json')
    if os.path.exists(key_path):
        cred = credentials.Certificate(key_path)
        firebase_admin.initialize_app(cred)
        print("Firebase: zainicjalizowano z pliku firebase_key.json")
        return firestore.client()

    raise RuntimeError("Brak konfiguracji Firebase!")

db = init_firebase()

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

app = Flask(__name__)
app.secret_key = os.getenv("SK", "fallback-secret-key-change-me")

# Funkcje pomocnicze do zarządzania danymi
def fs_get_doc(collection, doc_id, default=None):
    try:
        doc = db.collection(collection).document(doc_id).get()
        return doc.to_dict() if doc.exists else default
    except Exception as e:
        print(f"Firestore GET błąd ({collection}/{doc_id}): {e}")
        return default

def fs_set_doc(collection, doc_id, data):
    try:
        db.collection(collection).document(doc_id).set(data)
        return True
    except Exception as e:
        print(f"Firestore SET błąd ({collection}/{doc_id}): {e}")
        return False

def fs_get_collection(collection, default=None):
    try:
        docs = db.collection(collection).stream()
        result = {doc.id: doc.to_dict() for doc in docs}
        return result if result else (default if default is not None else {})
    except Exception as e:
        print(f"Firestore GET kolekcja błąd ({collection}): {e}")
        return default if default is not None else {}

def fs_get_list(collection, doc_id):
    try:
        doc = db.collection(collection).document(doc_id).get()
        return doc.to_dict().get('items', []) if doc.exists else []
    except Exception as e:
        print(f"Firestore GET LIST błąd ({collection}/{doc_id}): {e}")
        return []

def allowed_file(filename):
    """Sprawdza czy plik ma dozwolone rozszerzenie"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_task_content(task_data):
    """Zwraca treść zadania — obsługuje stary format (string) i nowy (dict)"""
    if isinstance(task_data, dict):
        return task_data.get("tresc", "")
    return task_data  # stary format — czysty string

def get_task_name(task_data, task_id):
    """Zwraca nazwę zadania — fallback na skrócone ID"""
    if isinstance(task_data, dict):
        return task_data.get("nazwa", task_id[:8])
    return task_id[:8]

def load_users():
    data = fs_get_collection('users')
    if data:
        return data
    # Fallback: users.json
    local_path = os.path.join(DATA_DIR, 'users.json')
    if os.path.exists(local_path):
        with open(local_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for username, udata in data.items():
            fs_set_doc('users', username, udata)
        return data
    # Fallback: users.py
    from users import USERS as DEFAULT_USERS
    for username, udata in DEFAULT_USERS.items():
        fs_set_doc('users', username, udata)
    return DEFAULT_USERS

def load_tasks():
    data = fs_get_collection('tasks')
    if data:
        return data
    local_path = os.path.join(DATA_DIR, 'tasks.json')
    if os.path.exists(local_path):
        with open(local_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for task_id, tdata in data.items():
            if isinstance(tdata, str):
                tdata = {"nazwa": "", "tresc": tdata}
            fs_set_doc('tasks', task_id, tdata)
        return data
    from tasks import TASKS as DEFAULT_TASKS
    for task_id, tdata in DEFAULT_TASKS.items():
        if isinstance(tdata, str):
            tdata = {"nazwa": "", "tresc": tdata}
        fs_set_doc('tasks', task_id, tdata)
    return DEFAULT_TASKS

def load_locations():
    return fs_get_collection('locations', {})

def load_task_times():
    data = fs_get_list('task_times', 'all')
    if not data:
        local_path = os.path.join(DATA_DIR, 'task_times.json')
        if os.path.exists(local_path):
            with open(local_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data:
                fs_set_doc('task_times', 'all', {'items': data})
    return data or []

def load_solutions():
    raw = fs_get_collection('solutions', {})
    result = {username: set(udata.get('solved', [])) for username, udata in raw.items()}
    if not result:
        local_path = os.path.join(DATA_DIR, 'zadania_rozwiazania.json')
        if os.path.exists(local_path):
            with open(local_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for username, solved in data.items():
                result[username] = set(solved) if isinstance(solved, list) else set()
            for username, solved_set in result.items():
                fs_set_doc('solutions', username, {'solved': list(solved_set)})
    return result

# Ładowanie danych przy starcie
CURRENT_USERS = load_users()
CURRENT_TASKS = load_tasks()
players_location = load_locations()
task_times = load_task_times()
zadania_rozwiazania = load_solutions()

zadania_czasy = {}  # Tymczasowe dane sesji

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            return render_template("login.html", error="Proszę podać nazwę użytkownika i hasło")

        user = CURRENT_USERS.get(username)
        if user and user["password"] == password:
            session["username"] = username
            session["role"] = user["role"]
            return redirect(url_for("dashboard"))

        return render_template("login.html", error="Nieprawidłowe dane")

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect(url_for("login"))

    if session["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    else:
        return redirect(url_for("player_dashboard"))

@app.route("/player")
def player_dashboard():
    if session.get("role") != "player":
        return redirect(url_for("login"))
    return render_template("player_dashboard.html", username=session["username"])

@app.route("/admin")
def admin_dashboard():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    return render_template("admin_dashboard.html", username=session["username"])

@app.route("/update_location", methods=["POST"])
def update_location():
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Brak danych JSON"}), 400

        latitude = data.get("latitude")
        longitude = data.get("longitude")

        if latitude is None or longitude is None:
            return jsonify({"error": "Nieprawidłowe dane lokalizacji"}), 400

        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (ValueError, TypeError):
            return jsonify({"error": "Nieprawidłowy format współrzędnych"}), 400

        # Sprawdź czy współrzędne są w rozsądnym zakresie
        if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
            return jsonify({"error": "Współrzędne poza dozwolonym zakresem"}), 400

        username = session["username"]
        
        # Dodaj więcej informacji do zapisywanej lokalizacji
        location_data = {
            "latitude": latitude,
            "longitude": longitude,
            "last_update": datetime.utcnow().isoformat() + "Z",
            "accuracy": data.get("accuracy"),
            "timestamp": data.get("timestamp"),
            "user_agent": request.headers.get('User-Agent', '')[:100]  # Ograniczone do 100 znaków
        }
        
        players_location[username] = location_data
        
        # Zapisz do pliku z obsługą błędów
        if fs_set_doc('locations', username, location_data):
            return jsonify({"status": "success", "message": "Lokalizacja zaktualizowana"})
        else:
            return jsonify({"error": "Błąd zapisu do Firestore"}), 500
            
    except Exception as e:
        print(f"Błąd podczas aktualizacji lokalizacji: {e}")
        return jsonify({"error": "Wewnętrzny błąd serwera"}), 500

@app.route("/get_locations")
def get_locations():
    if "username" not in session or session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        # Przefiltruj stare lokalizacje (starsze niż 24 godziny)
        current_time = datetime.utcnow()
        filtered_locations = {}
        
        for username, location in players_location.items():
            try:
                last_update = datetime.fromisoformat(location["last_update"].replace('Z', '+00:00'))
                time_diff = (current_time - last_update.replace(tzinfo=None)).total_seconds() / 3600
                
                # Zachowaj lokalizacje młodsze niż 24 godziny
                if time_diff < 24:
                    filtered_locations[username] = location
                else:
                    print(f"DEBUG: Odfiltrowano starą lokalizację {username} ({time_diff:.1f}h)")
            except (ValueError, KeyError) as e:
                print(f"DEBUG: Błąd parsowania daty dla {username}: {e}")
                # Zachowaj lokalizacje z błędami daty (mogą być nowe)
                filtered_locations[username] = location
        
        return jsonify(filtered_locations)
        
    except Exception as e:
        print(f"Błąd podczas pobierania lokalizacji: {e}")
        return jsonify({"error": "Błąd pobierania danych"}), 500

@app.route("/zadanie/<task_id>")
def pokaz_zadanie(task_id):
    username = session.get("username")
    if not username:
        return redirect(url_for("login"))
    
    if task_id not in CURRENT_TASKS:
        return redirect(url_for("player_dashboard"))

    # Sprawdź czy użytkownik już rozwiązał to zadanie
    user_solutions = zadania_rozwiazania.get(username, set())
    if isinstance(user_solutions, list):
        user_solutions = set(user_solutions)
    
    if task_id in user_solutions:
        return redirect(url_for("player_dashboard"))

    # Inicjalizuj czas rozpoczęcia
    if username not in zadania_czasy:
        zadania_czasy[username] = {}

    if task_id not in zadania_czasy[username]:
        zadania_czasy[username][task_id] = {"start": datetime.utcnow(), "end": None}

    start_time = zadania_czasy[username][task_id]["start"]
    start_time_iso = start_time.isoformat() + "Z"
    end_time = zadania_czasy[username][task_id]["end"]
    end_time_iso = (end_time.isoformat() + "Z") if end_time else None

    tresc = get_task_content(CURRENT_TASKS[task_id])
    return render_template("zadanie.html", 
                         task_id=task_id, 
                         tresc=tresc, 
                         start_time_iso=start_time_iso, 
                         end_time_iso=end_time_iso, 
                         username=username)

@app.route("/zakoncz_zadanie/<task_id>", methods=["POST"])
def zakoncz_zadanie(task_id):
    username = session.get("username")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
        
    if username not in zadania_czasy or task_id not in zadania_czasy[username]:
        return jsonify({"error": "Brak danych o zadaniu"}), 400

    zadania_czasy[username][task_id]["end"] = datetime.utcnow()
    return jsonify({"status": "zakończono", "task_id": task_id})

@app.route("/upload_solution/<task_id>", methods=["POST"])
def upload_solution(task_id):
    username = session.get("username")
    if not username:
        return jsonify({"error": "Unauthorized"}), 401

    if task_id not in CURRENT_TASKS:
        return jsonify({"error": "Nieprawidłowe zadanie"}), 400

    try:
        # Sprawdź czy użytkownik już wysłał rozwiązanie
        if username not in zadania_rozwiazania:
            zadania_rozwiazania[username] = set()
        elif isinstance(zadania_rozwiazania[username], list):
            zadania_rozwiazania[username] = set(zadania_rozwiazania[username])

        if task_id in zadania_rozwiazania[username]:
            return jsonify({"status": "already_sent", "message": "Rozwiązanie już zostało wysłane"}), 200

        # Sprawdź plik
        if 'file' not in request.files:
            return jsonify({"error": "Brak pliku"}), 400
            
        file = request.files['file']
        if file.filename == "":
            return jsonify({"error": "Nie wybrano pliku"}), 400

        if file.filename and not allowed_file(file.filename):
            return jsonify({"error": "Nieprawidłowy typ pliku. Dozwolone: png, jpg, jpeg, gif"}), 400

        # Bezpieczna nazwa pliku
        original_filename = secure_filename(file.filename) if file.filename else "image.jpg"
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        task_name = get_task_name(CURRENT_TASKS.get(task_id, task_id), task_id)
        safe_task_name = secure_filename(task_name)
        filename = secure_filename(f"{username}_{safe_task_name}_{timestamp}.jpg")

        public_id = f"solutions/{username}/{filename}"
        upload_result = cloudinary.uploader.upload(
            file,
            public_id=public_id,
            resource_type="image",
            overwrite=True
        )
        image_url = upload_result.get("secure_url")
        print(f"DEBUG: Plik wysłany do Cloudinary: {image_url}")

        # Dodaj do rozwiązań
        zadania_rozwiazania[username].add(task_id)

        # Oblicz czas wykonania
        if username in zadania_czasy and task_id in zadania_czasy[username]:
            start = zadania_czasy[username][task_id]["start"]
            end = datetime.utcnow()
            zadania_czasy[username][task_id]["end"] = end
            duration = str(end - start)
        else:
            start = datetime.utcnow()
            end = datetime.utcnow()
            duration = "0:00:00"

        task_data = CURRENT_TASKS.get(task_id, {})
        max_minutes = None
        if isinstance(task_data, dict):
            try:
                max_minutes = float(task_data.get("max_minutes") or 0) or None
            except (ValueError, TypeError):
                max_minutes = None

        duration_seconds = (end - start).total_seconds()
        if max_minutes and duration_seconds > (max_minutes * 60 + 5):
            overtime_minutes = math.ceil((duration_seconds - max_minutes * 60) / 60)
            multiplier = round(1 - overtime_minutes * 0.2, 2)
        else:
            multiplier = 1.0

        # Dodaj rekord do task_times
        record = {
            "username": username,
            "task_id": task_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "duration": duration,
            "filename": filename,
            "original_filename": original_filename,
            "image_url": image_url,
            "file_size": upload_result.get("bytes", 0)
            "multiplier": multiplier,
            "max_minutes": max_minutes,
        }
        task_times.append(record)

        # Zapisz dane do plików
        # Konwertuj set na list dla JSON
        solutions_for_json = {}
        for user, solutions in zadania_rozwiazania.items():
            solutions_for_json[user] = list(solutions) if isinstance(solutions, set) else solutions

        fs_set_doc('solutions', username, {'solved': list(zadania_rozwiazania[username])})
        fs_set_doc('task_times', 'all', {'items': task_times})

        return jsonify({"status": "success", "message": "Rozwiązanie zostało wysłane"})

    except Exception as e:
        print(f"Błąd podczas uploadu: {e}")
        return jsonify({"error": f"Błąd podczas zapisywania pliku: {str(e)}"}), 500

@app.route("/get_task_times")
def get_task_times():
    if "username" not in session or session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401

    try:
        enriched = []
        for record in task_times:
            r = record.copy()
            task_id = r.get("task_id", "")
            task_data = CURRENT_TASKS.get(task_id)
            r["task_name"] = get_task_name(task_data, task_id) if task_data else task_id[:8]
            r["multiplier"] = record.get("multiplier", 1.0)
            r["max_minutes"] = record.get("max_minutes", None)
            enriched.append(r)
        return jsonify(enriched)
    except Exception as e:
        print(f"Błąd podczas pobierania czasów: {e}")
        return jsonify({"error": "Błąd pobierania czasów"}), 500

@app.route("/get_gallery")
def get_gallery():
    """Endpoint do pobierania listy zdjęć w galerii"""
    if "username" not in session or session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401

    gallery = []
    try:
        result = cloudinary.api.resources(
            type="upload",
            prefix="solutions/",
            max_results=500
        )
        for resource in result.get("resources", []):
            public_id = resource["public_id"]
            parts = public_id.split("/")
            # public_id wygląda tak: solutions/username/filename
            if len(parts) >= 3:
                username = parts[1]
                filename = parts[2]
                gallery.append({
                    "username": username,
                    "filename": filename,
                    "image_url": resource["secure_url"],
                    "file_exists": True,
                    "file_size": resource.get("bytes", 0)
                })
    except Exception as e:
        print(f"Błąd pobierania galerii z Cloudinary: {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify(gallery)

@app.route('/uploads/solutions/<user>/<filename>')
def uploaded_file(user, filename):
    if "username" not in session or session.get("role") != "admin":
        return "Unauthorized", 401

    public_id = f"solutions/{user}/{filename}"
    url = cloudinary.utils.cloudinary_url(public_id)[0]
    return redirect(url)

@app.route("/debug_files")
def debug_files():
    """Endpoint do debugowania - pokazuje strukturę plików"""
    if "username" not in session or session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    debug_info = {
        "upload_folder_exists": os.path.exists(UPLOAD_FOLDER),
        "upload_folder_path": UPLOAD_FOLDER,
        "current_working_directory": os.getcwd(),
        "base_dir": BASE_DIR,
        "static_folder": app.static_folder,
        "files_structure": {},
        "total_files": 0,
        "total_size": 0
    }
    
    try:
        if os.path.exists(UPLOAD_FOLDER):
            for user in os.listdir(UPLOAD_FOLDER):
                user_path = os.path.join(UPLOAD_FOLDER, user)
                if os.path.isdir(user_path):
                    user_files = []
                    user_total_size = 0
                    
                    for filename in os.listdir(user_path):
                        file_path = os.path.join(user_path, filename)
                        if os.path.isfile(file_path):
                            file_size = os.path.getsize(file_path)
                            user_files.append({
                                "name": filename,
                                "size": file_size,
                                "path": file_path,
                                "is_image": filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))
                            })
                            user_total_size += file_size
                            debug_info["total_files"] += 1
                            debug_info["total_size"] += file_size
                    
                    debug_info["files_structure"][user] = {
                        "path": user_path,
                        "files": user_files,
                        "total_files": len(user_files),
                        "total_size": user_total_size
                    }
    except Exception as e:
        debug_info["error"] = str(e)
    
    return jsonify(debug_info)

@app.route("/get_gallery_images")
def get_gallery_images():
    """Alternatywny endpoint dla galerii (kompatybilność z HTML)"""
    return get_gallery()

@app.route("/test_geolocation")
def test_geolocation():
    """Endpoint do testowania wsparcia geolokalizacji"""
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify({
        "username": session["username"],
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "user_agent": request.headers.get('User-Agent', ''),
        "is_https": request.is_secure,
        "protocol": request.scheme,
        "host": request.host,
        "remote_addr": request.remote_addr,
        "headers": dict(request.headers)
    })

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.errorhandler(404)
def not_found(error):
    return render_template('login.html', error="Strona nie została znaleziona"), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('login.html', error="Wystąpił błąd serwera"), 500

@app.route("/api/users", methods=["GET"])
def get_users():
    """Pobiera listę użytkowników dla panelu Settings"""
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    # Zwraca użytkowników z hasłami (do edycji)
    return jsonify(CURRENT_USERS)

@app.route("/api/users", methods=["POST"])
def update_users():
    """Zapisuje zaktualizowanych użytkowników"""
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Brak danych"}), 400
        # Blokada usunięcia własnego profilu
        if session.get("username") not in data:
            return jsonify({"error": "Nie możesz usunąć własnego profilu"}), 400
        # Walidacja - każdy użytkownik musi mieć login, hasło i rolę
        for username, user_data in data.items():
            if not username.strip():
                return jsonify({"error": "Nazwa użytkownika nie może być pusta"}), 400
            if not user_data.get("password", "").strip():
                return jsonify({"error": f"Hasło dla użytkownika '{username}' nie może być puste"}), 400
            if user_data.get("role") not in ["admin", "player"]:
                return jsonify({"error": f"Nieprawidłowa rola dla użytkownika '{username}'"}), 400
        
        # Sprawdź czy pozostaje przynajmniej jeden admin
        admin_count = sum(1 for user in data.values() if user.get("role") == "admin")
        if admin_count == 0:
            return jsonify({"error": "Musi pozostać przynajmniej jeden administrator"}), 400
        
        # Aktualizuj globalne dane
        global CURRENT_USERS
        CURRENT_USERS = data.copy()
        
        # Zapisz do pliku
        for username, udata in CURRENT_USERS.items():
            fs_set_doc('users', username, udata)

        # Usuń użytkowników których już nie ma
        existing = fs_get_collection('users', {})
        for old_username in existing:
            if old_username not in CURRENT_USERS:
                db.collection('users').document(old_username).delete()
                try:
                    cloudinary.api.delete_resources_by_prefix(f"solutions/{old_username}/")
                    print(f"Usunięto zdjęcia użytkownika {old_username} z Cloudinary")

                    # Usuń rozwiązania z Firestore
                    db.collection('solutions').document(old_username).delete()
                    if old_username in zadania_rozwiazania:
                        del zadania_rozwiazania[old_username]

                    # Usuń lokalizację
                    db.collection('locations').document(old_username).delete()
                    if old_username in players_location:
                        del players_location[old_username]

                    # Usuń czasy zadań — przefiltruj listę
                    global task_times
                    task_times = [r for r in task_times if r.get("username") != old_username]
                    fs_set_doc('task_times', 'all', {'items': task_times})

                    # Usuń dane sesji jeśli gracz był w trakcie zadania
                    if old_username in zadania_czasy:
                        del zadania_czasy[old_username]

                    print(f"Usunięto wszystkie dane użytkownika {old_username}")
                except Exception as e:
                    print(f"Błąd usuwania zdjęć {old_username}: {e}")

        return jsonify({"status": "success", "message": f"Zaktualizowano {len(data)} użytkowników"})
            
    except Exception as e:
        print(f"Błąd aktualizacji użytkowników: {e}")
        return jsonify({"error": f"Wewnętrzny błąd serwera: {str(e)}"}), 500

@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    """Pobiera listę zadań dla panelu Settings"""
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify(CURRENT_TASKS)

@app.route("/api/tasks", methods=["POST"])
def update_tasks():
    """Zapisuje zaktualizowane zadania"""
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Brak danych"}), 400
        
        # Walidacja - każde zadanie musi mieć ID i treść
        for task_id, content in data.items():
            if not task_id.strip():
                return jsonify({"error": "ID zadania nie może być puste"}), 400
                # Obsługa starego formatu (string) i nowego (dict)
            if isinstance(content, dict):
                if not content.get("tresc", "").strip():
                    return jsonify({"error": f"Treść zadania '{task_id}' nie może być pusta"}), 400
            else:
                if not content.strip():
                    return jsonify({"error": f"Treść zadania '{task_id}' nie może być pusta"}), 400
        
        # Aktualizuj globalne dane
        global CURRENT_TASKS
        CURRENT_TASKS = data.copy()
        
        # Zapisz do pliku
        for task_id, tdata in CURRENT_TASKS.items():
            if isinstance(tdata, str):
                tdata = {"nazwa": "", "tresc": tdata}
                CURRENT_TASKS[task_id] = tdata
            fs_set_doc('tasks', task_id, tdata)

        # Usuń zadania których już nie ma
        existing = fs_get_collection('tasks', {})
        for old_id in existing:
            if old_id not in CURRENT_TASKS:
                db.collection('tasks').document(old_id).delete()

        return jsonify({"status": "success", "message": f"Zaktualizowano {len(data)} zadań"})
            
    except Exception as e:
        print(f"Błąd aktualizacji zadań: {e}")
        return jsonify({"error": f"Wewnętrzny błąd serwera: {str(e)}"}), 500

@app.route("/api/ranking")
def get_ranking():
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    try:
        tasks = CURRENT_TASKS
        players = {u: v for u, v in CURRENT_USERS.items() if v.get("role") == "player"}

        # Buduj strukturę: task_id -> {player -> {multiplier, points, result}}
        ranking = {}
        for task_id, task_data in tasks.items():
            ranking[task_id] = {
                "task_name": get_task_name(task_data, task_id),
                "players": {}
            }
            for username in players:
                ranking[task_id]["players"][username] = {
                    "multiplier": 1.0,
                    "points": None,  # None = nauczyciel jeszcze nie wpisał
                    "result": None,
                    "completed": False,
                }

        # Uzupełnij mnożniki z task_times
        for record in task_times:
            tid = record.get("task_id")
            uname = record.get("username")
            if tid in ranking and uname in ranking[tid]["players"]:
                ranking[tid]["players"][uname]["multiplier"] = record.get("multiplier", 1.0)
                ranking[tid]["players"][uname]["completed"] = True

        # Uzupełnij punkty zapisane w Firestore
        saved_points = fs_get_doc("ranking_points", "all", {})
        for task_id, task_data in saved_points.items():
            if task_id in ranking:
                for username, pdata in task_data.items():
                    if username in ranking[task_id]["players"]:
                        points = pdata.get("points")
                        multiplier = ranking[task_id]["players"][username]["multiplier"]
                        ranking[task_id]["players"][username]["points"] = points
                        if points is not None:
                            ranking[task_id]["players"][username]["result"] = round(points / multiplier, 2)

        # Oblicz sumy dla rankingu końcowego
        totals = {u: 0.0 for u in players}
        for task_id, tdata in ranking.items():
            for username, pdata in tdata["players"].items():
                if pdata["result"] is not None:
                    totals[username] += pdata["result"]

        return jsonify({
            "tasks": ranking,
            "player_names": list(players.keys()),
            "totals": totals,
        })

    except Exception as e:
        print(f"Błąd rankingu: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ranking", methods=["POST"])
def save_ranking_points():
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Brak danych"}), 400

        # data = { task_id: { username: { points: X } } }
        fs_set_doc("ranking_points", "all", data)
        return jsonify({"status": "success"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)