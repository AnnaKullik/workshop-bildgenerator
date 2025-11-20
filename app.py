import os
import io
import json
import base64
import datetime
import time
import requests
from flask import Flask, request, send_file, session
from markupsafe import Markup
from PIL import Image

# (HEIC-Unterstützung vorbereitet, aktuell aber nicht genutzt)
HEIF_AVAILABLE = False
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except Exception:
    HEIF_AVAILABLE = False

# ==============================================
#   KONFIGURATION AUS UMGEBUNGSVARIABLEN
# ==============================================
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
APP_SECRET = os.getenv("APP_SECRET", "change_me_please")

# 60 Minuten Login-Gültigkeit
SESSION_MAX_AGE_SECONDS = 60 * 60

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
LAST_IMAGE_PATH = os.path.join(OUTPUT_DIR, "last_image.png")


# ==============================================
#   HELFERFUNKTIONEN
# ==============================================
def save_bytes_to_last_image(data: bytes) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(LAST_IMAGE_PATH, "wb") as f:
        f.write(data)
    session["last_image_path"] = LAST_IMAGE_PATH


def get_last_image_bytes() -> bytes:
    p = session.get("last_image_path", LAST_IMAGE_PATH)
    if p and os.path.exists(p):
        with open(p, "rb") as f:
            return f.read()
    raise RuntimeError("Kein vorheriges Bild vorhanden. Bitte zuerst eines erzeugen.")


def slugify(text, limit=60):
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ "
    s = "".join(ch if ch in allowed else "_" for ch in text)
    s = "_".join(s.strip().split())
    s = s[:limit].strip("_")
    return s if s else "bild"


def build_filename(user_filename, prompt):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = slugify(user_filename) if user_filename else slugify(prompt, 48)
    if not base.lower().endswith(".png"):
        base += ".png"
    return f"{ts}_{base}"


def build_result_html(image_data_url, prompt, message=""):
    if not image_data_url:
        return '<p class="small">Noch kein Bild erzeugt.</p>'

    preview_html = f'<img src="{image_data_url}" alt="Erzeugtes Bild">'
    prompt_esc = Markup.escape(prompt or "")
    default_name = slugify(prompt or "bild") + ".png"
    note_html = f'<p class="small">{Markup.escape(message)}</p>' if message else ""

    return f"""
      <div class="out">
        <p><strong>Fertig!</strong></p>
        {preview_html}

        <form method="post" action="/download" style="margin-top:16px">
          <input type="hidden" name="prompt" value="{prompt_esc}">
          <label>Dateiname (optional)</label>
          <input type="text" name="filename" placeholder="{default_name}">
          <div class="actions">
            <button type="submit" class="btn-secondary">Download</button>
          </div>
        </form>

        {note_html}
      </div>
    """


def build_pw_block(auth_ok: bool) -> str:
    """
    Baut den HTML-Block für das Passwortfeld.
    - Wenn eingeloggt: gar nichts anzeigen.
    - Wenn nicht eingeloggt oder Session abgelaufen: Feld mit Platzhalter anzeigen.
    """
    if auth_ok:
        return ""  # Passwortfeld komplett ausblenden
    return """
      <label>Workshop-Passwort</label>
      <input name="pw" type="password" placeholder="Passwort eingeben" required>
    """


# ==============================================
#   HTML / CSS – MOBIL OPTIMIERT
# ==============================================
PAGE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Workshop – Bildgenerator</title>

<style>

:root {
  --bg:#f7f5f2;
  --card:#ffffff;
  --text:#222;
  --muted:#6b6b6b;
  --accent:#0e7a7a;
}

/* —— Grundlayout —— */
body {
  margin:0;
  background:var(--bg);
  font:16px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,system-ui,Roboto,Arial,sans-serif;
}

.wrap {
  max-width:860px;
  margin:48px auto;
  padding:0 16px;
}

.card {
  background:var(--card);
  box-shadow:0 8px 30px rgba(0,0,0,.07);
  border-radius:20px;
  padding:24px;
}

/* —— Überschrift —— */
h1 {
  margin:0 0 16px;
  font-size:28px;
}

/* —— Texte —— */
p.hint {
  color:var(--muted);
  margin:0 0 24px;
  font-size:15px;
}

.small {
  color:var(--muted);
  font-size:13px;
}

/* —— Labels —— */
label {
  display:block;
  margin:16px 0 6px;
  font-weight:600;
  font-size:16px;
}

/* —— Eingabefelder —— */
input[type=password],
input[type=text],
textarea,
select,
input[type=file] {
  width:100%;
  padding:18px 18px;
  border:1px solid #ddd;
  border-radius:14px;
  font-size:18px;
  outline:none;
}

/* großes Prompt-Feld */
textarea {
  min-height:350px;
  resize:vertical;
}

/* Datei auswählen – Button im Input selbst */
input[type=file]::file-selector-button,
input[type=file]::-webkit-file-upload-button {
  padding:10px 18px;
  margin-right:12px;
  border-radius:12px;
  border:none;
  background:var(--accent);
  color:#fff;
  font-weight:600;
  font-size:16px;
  cursor:pointer;
}

/* —— Buttons —— */
button {
  appearance:none;
  border:0;
  background:var(--accent);
  color:#fff;
  font-weight:700;
  padding:14px 16px;
  border-radius:14px;
  cursor:pointer;
  font-size:18px;
}

.btn-secondary {
  background:#2c7a7b;
}

/* kleiner Abmelde-Button */
.logout {
  background:#b00020!important;
  padding:6px 10px!important;
  font-size:14px!important;
  border-radius:8px!important;
}

/* —— Button-Gruppe —— */
.actions {
  display:flex;
  gap:12px;
  flex-wrap:wrap;
  margin-top:16px;
}

/* —— Bild —— */
img {
  max-width:100%;
  display:block;
  border-radius:18px;
  margin-top:12px;
}

.out { margin-top:24px; }

/* —— Handy ——— */
@media (max-width:720px) {

  h1 {
    font-size:22px;
  }

  p.hint {
    font-size:14px;
  }

  .wrap {
    margin:24px auto;
    padding:0 12px;
  }

  .card {
    padding:20px;
  }

  button,
  .actions button {
    width:100%;
    font-size:17px;
    padding:16px;
  }

  textarea {
    min-height:320px;
  }
}

</style>
</head>

<body>
  <div class="wrap">
    <div class="card">

      <div class="topbar" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <h1>Workshop: Bildgenerator</h1>
        <form method="post" action="/logout">
          <button class="logout">Abmelden</button>
        </form>
      </div>

      <p class="hint">
        1) Passwort eingeben (nur beim Start oder nach Timeout) · 
        2) JPG/PNG-Foto hochladen oder letztes Bild nutzen · 
        3) Prompt eingeben · 4) Bild erzeugen · 5) Download
      </p>

      <form method="post" action="/" enctype="multipart/form-data">

        {{PW_BLOCK}}

        <label>Größe</label>
        <select name="size">
          <option value="match_upload" selected>Format wie Upload-Bild</option>
          <option value="auto">Auto</option>
          <option value="1536x1024">Quer 1536×1024</option>
          <option value="1024x1536">Hoch 1024×1536</option>
          <option value="1024x1024">Quadrat 1024×1024</option>
        </select>

        <label>Eigenes Ausgangsbild (optional, nur JPG/PNG)</label>
        <input type="file" name="image" accept=".jpg,.jpeg,.png,image/jpeg,image/png">

        <label style="margin-top:8px;">
          <input type="checkbox" name="use_last_image" value="1">
          Mit dem zuletzt erzeugten Bild weiterarbeiten
        </label>

        <label>Prompt</label>
        <textarea name="prompt" placeholder="Beschreibe dein Bild …" required>{{prompt}}</textarea>

        <div style="margin-top:16px;display:flex;gap:10px;align-items:center">
          <button type="submit">Bild erzeugen</button>
        </div>
      </form>

      <div class="out">{{result}}</div>

    </div>

    <p class="small">Lokal oder auf Server. Bitte nur JPG/PNG hochladen (keine HEIC-Dateien).</p>
  </div>
</body>
</html>"""


# ==============================================
#   HILFSFUNKTION ZUM RENDERN DER SEITE
# ==============================================
def render_page(result_html: str, prompt_value: str) -> str:
    auth_ok = session.get("auth_ok", False)
    pw_block = build_pw_block(auth_ok)
    page = (
        PAGE.replace("{{result}}", result_html)
            .replace("{{prompt}}", Markup.escape(prompt_value or ""))
            .replace("{{PW_BLOCK}}", pw_block)
    )
    return page


# ==============================================
#   FLASK-APP
# ==============================================
app = Flask(__name__)
app.secret_key = APP_SECRET


@app.route("/", methods=["GET", "POST"])
def index():
    auth_ok = session.get("auth_ok", False)
    auth_time = session.get("auth_time")

    # Session abgelaufen?
    if auth_ok and auth_time:
        if time.time() - auth_time > SESSION_MAX_AGE_SECONDS:
            session.clear()
            auth_ok = False

    prompt_value = session.get("last_prompt", "")
    result_html = ""

    if request.method == "POST":

        try:
            if not OPENAI_API_KEY:
                raise RuntimeError("OPENAI_API_KEY ist nicht gesetzt.")
            if not APP_PASSWORD:
                raise RuntimeError("APP_PASSWORD ist nicht gesetzt.")

            # Passwort-Check nur, wenn nicht eingeloggt
            if not auth_ok:
                pw = request.form.get("pw", "").strip()
                if pw != APP_PASSWORD:
                    raise RuntimeError("Falsches Passwort.")
                session["auth_ok"] = True
                session["auth_time"] = time.time()
                auth_ok = True

            prompt = request.form.get("prompt", "").strip()
            size_choice = request.form.get("size", "match_upload").strip()
            uploaded = request.files.get("image")
            use_last = request.form.get("use_last_image") == "1"

            if not prompt:
                raise RuntimeError("Bitte Prompt eingeben.")

            session["last_prompt"] = prompt
            prompt_value = prompt

            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}

            allowed_sizes = ("1024x1024", "1536x1024", "1024x1536")
            data_json = None
            info_msg = ""

            # ===== UPLOAD-BILD =====
            if uploaded and uploaded.filename:
                img_bytes = uploaded.read()

                # Dateigröße prüfen
                file_size_mb = len(img_bytes) / (1024 * 1024)
                if file_size_mb > 10:
                    info_msg += (
                        f"Hinweis: Die hochgeladene Datei ist relativ groß (~{file_size_mb:.1f} MB). "
                        f"Falls etwas schiefgeht, bitte vorher auf dem Gerät verkleinern. "
                    )

                # HEIC/HEIF explizit abweisen
                filename_lower = uploaded.filename.lower()
                if filename_lower.endswith(".heic") or filename_lower.endswith(".heif"):
                    raise RuntimeError(
                        "HEIC/HEIF-Dateien werden aktuell nicht unterstützt. "
                        "Bitte das Bild vorher als JPG oder PNG speichern und erneut hochladen."
                    )

                # Bild einlesen
                try:
                    pil = Image.open(io.BytesIO(img_bytes))
                    w, h = pil.size
                    ratio = w / float(h)
                except Exception:
                    pil = None
                    ratio = 1

                # Format-Ermittlung
                if size_choice in ("match_upload", "auto"):
                    if 0.9 <= ratio <= 1.1:
                        size = "1024x1024"
                    elif ratio > 1.1:
                        size = "1536x1024"
                    else:
                        size = "1024x1536"
                else:
                    size = size_choice if size_choice in allowed_sizes else "1024x1024"

                # Bild nach PNG normalisieren
                try:
                    pil = pil.convert("RGBA")
                    buff = io.BytesIO()
                    pil.save(buff, format="PNG")
                    img_bytes = buff.getvalue()
                except Exception:
                    pass

                files = {"image": ("image.png", img_bytes, "image/png")}
                data = {"model": "gpt-image-1", "prompt": prompt, "size": size}

                r = requests.post(
                    "https://api.openai.com/v1/images/edits",
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=120
                )
                r.raise_for_status()
                data_json = r.json()

            # ===== WEITERARBEITEN MIT LETZTEM BILD =====
            elif use_last:
                img_bytes = get_last_image_bytes()

                try:
                    pil = Image.open(io.BytesIO(img_bytes))
                    w, h = pil.size
                    ratio = w / float(h)
                except Exception:
                    pil = None
                    ratio = 1

                if size_choice in ("match_upload", "auto"):
                    if 0.9 <= ratio <= 1.1:
                        size = "1024x1024"
                    elif ratio > 1.1:
                        size = "1536x1024"
                    else:
                        size = "1024x1536"
                else:
                    size = size_choice if size_choice in allowed_sizes else "1024x1024"

                try:
                    pil = pil.convert("RGBA")
                    buff = io.BytesIO()
                    pil.save(buff, format="PNG")
                    img_bytes = buff.getvalue()
                except Exception:
                    pass

                files = {"image": ("image.png", img_bytes, "image/png")}
                data = {"model": "gpt-image-1", "prompt": prompt, "size": size}

                r = requests.post(
                    "https://api.openai.com/v1/images/edits",
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=120
                )
                r.raise_for_status()
                data_json = r.json()

            # ===== NEUES BILD (OHNE UPLOAD) ERZEUGEN =====
            else:
                if size_choice in allowed_sizes or size_choice == "auto":
                    size = size_choice
                else:
                    size = "1024x1024"

                body = {"model": "gpt-image-1", "prompt": prompt, "size": size}

                r = requests.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={**headers, "Content-Type": "application/json"},
                    data=json.dumps(body),
                    timeout=120
                )
                r.raise_for_status()
                data_json = r.json()

            # ===== BILD EXTRAHIEREN =====
            item0 = data_json["data"][0]
            b64 = item0.get("b64_json")
            url = item0.get("url")

            if b64:
                data_bytes = base64.b64decode(b64)
            elif url:
                resp_img = requests.get(url, timeout=60)
                resp_img.raise_for_status()
                data_bytes = resp_img.content
            else:
                raise RuntimeError("Fehler: Keine Bilddaten enthalten.")

            save_bytes_to_last_image(data_bytes)

            data_b64 = base64.b64encode(data_bytes).decode("utf-8")
            image_data_url = "data:image/png;base64," + data_b64

            result_html = build_result_html(image_data_url, prompt, message=info_msg)

        except Exception as e:
            esc = Markup.escape(str(e))
            result_html = f'<p class="small" style="color:#b00020">Fehler: {esc}</p>'

    return render_page(result_html, prompt_value)


@app.route("/download", methods=["POST"])
def download():
    prompt = session.get("last_prompt", "") or request.form.get("prompt", "")
    user_filename = request.form.get("filename", "")
    try:
        data = get_last_image_bytes()
        filename = build_filename(user_filename, prompt)
        return send_file(
            io.BytesIO(data),
            mimetype="image/png",
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        esc = Markup.escape(str(e))
        result_html = f'<p style="color:#b00020">Fehler beim Download: {esc}</p>'
        return render_page(result_html, prompt or "")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    # Nach Logout: wieder leere Seite, Passwortfeld sichtbar
    return render_page("", "")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5051"))
    app.run(host="127.0.0.1", port=port, debug=False)
