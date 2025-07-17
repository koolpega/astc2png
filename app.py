from flask import Flask, request, send_file, render_template
import subprocess
import os
import platform
from PIL import Image
import io

app = Flask(__name__)

# Select astcenc binary based on OS
if platform.system() == "Windows":
    ASTCENC_PATH = "./bin/astcenc-avx2.exe"
else:
    ASTCENC_PATH = "./bin/astcenc-avx2"

@app.route("/", methods=["GET", "POST"])
def convert():
    if request.method == "POST":
        if "file" not in request.files:
            return render_template("index.html", error="No file uploaded")
        file = request.files["file"]
        if not file.filename.endswith(".astc"):
            return render_template("index.html", error="Invalid file format. Please upload an .astc file")

        # Save uploaded file temporarily
        astc_path = "temp.astc"
        tga_path = "temp.tga"
        file.save(astc_path)

        try:
            # Verify astcenc binary exists and is executable
            if not os.path.exists(ASTCENC_PATH):
                return render_template("index.html", error=f"astcenc binary not found at {ASTCENC_PATH}")
            if not os.access(ASTCENC_PATH, os.X_OK):
                return render_template("index.html", error=f"astcenc binary is not executable: {ASTCENC_PATH}")

            # Call astcenc to decompress ASTC to TGA
            result = subprocess.run(
                [ASTCENC_PATH, "-dl", astc_path, tga_path],
                check=True,
                capture_output=True,
                text=True
            )
            app.logger.info(f"astcenc output: {result.stdout}")

            # Convert TGA to PNG and serve via BytesIO
            img = Image.open(tga_path)
            png_buffer = io.BytesIO()
            img.save(png_buffer, format="PNG")
            png_buffer.seek(0)

            # Send PNG file from memory
            response = send_file(
                png_buffer,
                mimetype="image/png",
                as_attachment=True,
                download_name="output.png"
            )
            return response
        except subprocess.CalledProcessError as e:
            app.logger.error(f"astcenc failed: {e.stderr}")
            return render_template("index.html", error=f"astcenc failed: {e.stderr}")
        except Exception as e:
            app.logger.error(f"Error: {str(e)}")
            return render_template("index.html", error=f"Error: {str(e)}")
        finally:
            # Clean up temporary files
            for path in [astc_path, tga_path]:
                if os.path.exists(path):
                    os.remove(path)

    return render_template("index.html", error=None)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=True)