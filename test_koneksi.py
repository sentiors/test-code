import requests
try:
    r = requests.get("https://oauth2.googleapis.com", timeout=10)
    print(f"Status Code: {r.status_code}")
    print("Koneksi ke Google API: BERHASIL")
except Exception as e:
    print(f"Koneksi ke Google API: GAGAL\nError: {e}")
