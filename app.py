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

app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)
app.config["SESSION_TYPE"] = "filesystem"  # Use filesystem for Render compatibility
app.config["SECRET_KEY"] = os.urandom(24)  # Secure session key
Session(app)

# Select astcenc binary based on OS
if platform.system() == "Windows":
    ASTCENC_PATH = "./bin/astcenc-avx2.exe"
else:
    ASTCENC_PATH = "./bin/astcenc-avx2"

@app.route("/", methods=["GET", "POST"])
def convert():
    if request.method == "GET":
        # Clear session on page load to reset state
        session.clear()
        return render_template("index.html", results=[], zip_available=False, error=None)

    if request.method == "POST":
        if "files" not in request.files:
            return render_template("index.html", error="No files uploaded", results=[])

        files = request.files.getlist("files")
        if not files or all(f.filename == "" for f in files):
            return render_template("index.html", error="No files selected", results=[])

        results = []
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file in files:
                if not file.filename.endswith(".astc"):
                    results.append({"filename": file.filename, "error": "Invalid file format"})
                    continue

                # Generate unique temporary filenames
                unique_id = str(uuid.uuid4())
                astc_path = f"temp_{unique_id}.astc"
                tga_path = f"temp_{unique_id}.tga"
                file.save(astc_path)

                try:
                    # Verify astcenc binary
                    if not os.path.exists(ASTCENC_PATH):
                        return render_template("index.html", error=f"astcenc binary not found at {ASTCENC_PATH}", results=[])

                    # Set executable permissions on Linux
                    if platform.system() != "Windows":
                        try:
                            os.chmod(ASTCENC_PATH, 0o755)
                            app.logger.info(f"Set executable permissions for {ASTCENC_PATH}")
                        except Exception as e:
                            app.logger.error(f"Failed to set permissions for {ASTCENC_PATH}: {str(e)}")

                    if not os.access(ASTCENC_PATH, os.X_OK):
                        return render_template("index.html", error=f"astcenc binary is not executable: {ASTCENC_PATH}", results=[])

                    # Validate ASTC file
                    with open(astc_path, "rb") as f:
                        if f.read(4) != b"\x13\xAB\xA1\x5C":
                            results.append({"filename": file.filename, "error": "Invalid ASTC file"})
                            continue

                    # Try different profiles
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
                            app.logger.info(f"astcenc output for {file.filename} with {profile}: {result.stdout}")

                            # Verify TGA file
                            if not os.path.exists(tga_path) or os.path.getsize(tga_path) == 0:
                                results.append({"filename": file.filename, "error": f"No output produced for {profile}"})
                                continue

                            # Convert TGA to PNG
                            img = Image.open(tga_path)
                            if img.size == (0, 0):
                                results.append({"filename": file.filename, "error": f"Empty image produced for {profile}"})
                                continue

                            # Resize for preview
                            img.thumbnail((200, 200))
                            png_buffer = io.BytesIO()
                            img.save(png_buffer, format="PNG")
                            png_buffer.seek(0)

                            # Store PNG in zip
                            png_filename = f"{os.path.splitext(file.filename)[0]}.png"
                            zip_file.writestr(png_filename, png_buffer.getvalue())

                            # Encode PNG for preview
                            png_base64 = base64.b64encode(png_buffer.getvalue()).decode("utf-8")
                            results.append({
                                "filename": file.filename,
                                "png_filename": png_filename,
                                "png_base64": png_base64
                            })
                            tga_valid = True
                            break
                        except subprocess.CalledProcessError as e:
                            app.logger.error(f"astcenc failed for {file.filename} with {profile}: {e.stderr}")
                            continue

                    if not tga_valid:
                        results.append({"filename": file.filename, "error": "Failed to convert with any profile"})
                except Exception as e:
                    app.logger.error(f"Error processing {file.filename}: {str(e)}")
                    results.append({"filename": file.filename, "error": f"Error: {str(e)}"})
                finally:
                    # Clean up temporary files
                    for path in [astc_path, tga_path]:
                        if os.path.exists(path):
                            os.remove(path)

        # Store zip in session and clear other session data
        session.clear()  # Clear session before storing new data
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
    zip_data = session.pop("zip_buffer")  # Clear from session
    return send_file(
        io.BytesIO(zip_data),
        mimetype="application/zip",
        as_attachment=True,
        download_name="converted_pngs.zip"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=True)
