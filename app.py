from flask import Flask, request, send_file, render_template, session
import subprocess
import os
import platform
from PIL import Image
import io
import zipfile
import uuid
import base64
import logging
from flask_session import Session
import requests  # NEW - to fetch remote files
import time

app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)
app.config["SESSION_TYPE"] = "filesystem"
app.config["SECRET_KEY"] = os.urandom(24)
Session(app)

if platform.system() == "Windows":
    ASTCENC_PATH = "./bin/astcenc-avx2.exe"
else:
    ASTCENC_PATH = "./bin/astcenc-avx2"

# Helper small wrapper so remote fetch can be handled in the same loop as uploaded files.
class RemoteFile:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    def save(self, dst_path: str):
        with open(dst_path, "wb") as f:
            f.write(self._data)

@app.route("/fetch_id", methods=["POST"])
def fetch_id():
    item_id = request.form.get("item_id")
    if not item_id or not item_id.isdigit():
        return render_template("index.html", error="Invalid Item ID", results=[])

    url = f"https://dl.cdn.freefiremobile.com/live/ABHotUpdates/IconCDN/android/{item_id}_rgb.astc"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return render_template("index.html", error=f"Failed to fetch ASTC file (status {response.status_code})", results=[])

        # Save temp ASTC file
        unique_id = str(uuid.uuid4())
        astc_path = f"temp_{unique_id}.astc"
        with open(astc_path, "wb") as f:
            f.write(response.content)

        # Reuse your existing ASTC -> PNG conversion logic
        tga_path = f"temp_{unique_id}.tga"
        results = []
        try:
            profiles = ["-dl", "-ds", "-dh", "-dH"]
            for profile in profiles:
                try:
                    subprocess.run(
                        [ASTCENC_PATH, profile, astc_path, tga_path],
                        check=True,
                        capture_output=True,
                        text=True
                    )
                    if os.path.exists(tga_path) and os.path.getsize(tga_path) > 0:
                        img = Image.open(tga_path)
                        img.thumbnail((200, 200))
                        png_buffer = io.BytesIO()
                        img.save(png_buffer, format="PNG")
                        png_buffer.seek(0)

                        png_filename = f"{item_id}.png"
                        png_base64 = base64.b64encode(png_buffer.getvalue()).decode("utf-8")
                        results.append({
                            "filename": f"{item_id}_rgb.astc",
                            "png_filename": png_filename,
                            "png_base64": png_base64
                        })
                        break
                except subprocess.CalledProcessError:
                    continue
            if not results:
                results.append({"filename": f"{item_id}_rgb.astc", "error": "Failed to convert with any profile"})
        finally:
            for path in [astc_path, tga_path]:
                if os.path.exists(path):
                    os.remove(path)

        return render_template("index.html", results=results, zip_available=False, error=None)

    except Exception as e:
        return render_template("index.html", error=f"Error fetching file: {str(e)}", results=[])


@app.route("/", methods=["GET", "POST"])
def convert():
    if request.method == "GET":
        return render_template("index.html", results=[], zip_available=False, error=None)

    if request.method == "POST":
        # Collect files from upload
        files = request.files.getlist("files") if "files" in request.files else []

        # Optional: fetch from CDN by item_id (single numeric id expected)
        item_id = (request.form.get("item_id") or "").strip()
        if item_id:
            if not item_id.isdigit():
                return render_template("index.html", error="Item ID must be numeric", results=[])
            cdn_url = f"https://dl.cdn.freefiremobile.com/live/ABHotUpdates/IconCDN/android/{item_id}_rgb.astc"
            try:
                # small timeout and user-agent to be polite
                resp = requests.get(cdn_url, timeout=10, headers={"User-Agent": "astc2png/1.0 (+https://github.com/yourname)"})
                if resp.status_code != 200:
                    return render_template("index.html", error=f"Failed to fetch item {item_id}: HTTP {resp.status_code}", results=[])
                content = resp.content
                # Optionally check that the fetched file is likely an ASTC by magic bytes
                if len(content) < 4 or content[:4] != b"\x13\xAB\xA1\x5C":
                    # not strictly necessary, but helps avoid wasting time processing bad content
                    return render_template("index.html", error=f"Fetched file for item {item_id} is not a valid ASTC", results=[])
                # wrap it so the normal file processing loop can use it
                remote_filename = f"{item_id}.astc"
                files.append(RemoteFile(remote_filename, content))
            except requests.RequestException as e:
                app.logger.error(f"Error fetching {cdn_url}: {str(e)}")
                return render_template("index.html", error=f"Error fetching item {item_id}: {str(e)}", results=[])

        # If there are no files at this stage, show error
        if not files or all(getattr(f, "filename", "") == "" for f in files):
            return render_template("index.html", error="No files selected or fetched", results=[])

        results = []
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file in files:
                # file could be either a werkzeug FileStorage or our RemoteFile wrapper
                filename = getattr(file, "filename", "")
                if not filename.endswith(".astc"):
                    results.append({"filename": filename, "error": "Invalid file format"})
                    continue

                unique_id = str(uuid.uuid4())
                astc_path = f"temp_{unique_id}.astc"
                tga_path = f"temp_{unique_id}.tga"

                try:
                    # save either uploaded file or remote file to disk for astcenc to read
                    file.save(astc_path)

                    if not os.path.exists(ASTCENC_PATH):
                        return render_template("index.html", error=f"astcenc binary not found at {ASTCENC_PATH}", results=[])

                    if platform.system() != "Windows":
                        try:
                            os.chmod(ASTCENC_PATH, 0o755)
                            app.logger.info(f"Set executable permissions for {ASTCENC_PATH}")
                        except Exception as e:
                            app.logger.error(f"Failed to set permissions for {ASTCENC_PATH}: {str(e)}")

                    if not os.access(ASTCENC_PATH, os.X_OK):
                        return render_template("index.html", error=f"astcenc binary is not executable: {ASTCENC_PATH}", results=[])

                    with open(astc_path, "rb") as f_read:
                        if f_read.read(4) != b"\x13\xAB\xA1\x5C":
                            results.append({"filename": filename, "error": "Invalid ASTC file"})
                            continue

                    profiles = ["-dl", "-ds", "-dh", "-dH"]
                    tga_valid = False
                    for profile in profiles:
                        try:
                            result = subprocess.run(
                                [ASTCENC_PATH, profile, astc_path, tga_path],
                                check=True,
                                capture_output=True,
                                text=True
                            )
                            app.logger.info(f"astcenc output for {filename} with {profile}: {result.stdout}")

                            if not os.path.exists(tga_path) or os.path.getsize(tga_path) == 0:
                                results.append({"filename": filename, "error": f"No output produced for {profile}"})
                                continue

                            img = Image.open(tga_path)
                            if img.size == (0, 0):
                                results.append({"filename": filename, "error": f"Empty image produced for {profile}"})
                                continue

                            img.thumbnail((200, 200))
                            png_buffer = io.BytesIO()
                            img.save(png_buffer, format="PNG")
                            png_buffer.seek(0)

                            png_filename = f"{os.path.splitext(filename)[0]}.png"
                            zip_file.writestr(png_filename, png_buffer.getvalue())

                            png_base64 = base64.b64encode(png_buffer.getvalue()).decode("utf-8")
                            results.append({
                                "filename": filename,
                                "png_filename": png_filename,
                                "png_base64": png_base64
                            })
                            tga_valid = True
                            break
                        except subprocess.CalledProcessError as e:
                            app.logger.error(f"astcenc failed for {filename} with {profile}: {e.stderr}")
                            continue

                    if not tga_valid:
                        results.append({"filename": filename, "error": "Failed to convert with any profile"})
                except Exception as e:
                    app.logger.error(f"Error processing {filename}: {str(e)}")
                    results.append({"filename": filename, "error": f"Error: {str(e)}"})
                finally:
                    for path in [astc_path, tga_path]:
                        if os.path.exists(path):
                            try:
                                os.remove(path)
                            except Exception as e:
                                app.logger.warning(f"Failed to remove {path}: {e}")

        if any("error" not in r for r in results):
            session["zip_buffer"] = zip_buffer.getvalue()
            return render_template(
                "index.html",
                results=results,
                zip_available=True,
                error=None
            )
        else:
            return render_template("index.html", results=results, zip_available=False, error="No valid ASTC files processed")

@app.route("/download_zip")
def download_zip():
    if "zip_buffer" not in session:
        return "No files to download", 400
    zip_data = session.pop("zip_buffer")
    return send_file(
        io.BytesIO(zip_data),
        mimetype="application/zip",
        as_attachment=True,
        download_name="converted_pngs.zip"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=True)

