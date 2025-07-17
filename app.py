from flask import Flask, request, send_file, render_template
import subprocess
import os
from PIL import Image
import io

app = Flask(__name__)

# Path to astcenc binary (Linux version)
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
        png_path = "output.png"
        file.save(astc_path)

        try:
            # Call astcenc to decompress ASTC to TGA
            subprocess.run([ASTCENC_PATH, "-dl", astc_path, tga_path], check=True)

            # Convert TGA to PNG
            Image.open(tga_path).save(png_path, "PNG")

            # Send PNG file
            return send_file(png_path, as_attachment=True, download_name="output.png")
        except subprocess.CalledProcessError as e:
            return render_template("index.html", error=f"astcenc failed: {e.stderr}")
        finally:
            # Clean up temporary files
            for path in [astc_path, tga_path, png_path]:
                if os.path.exists(path):
                    os.remove(path)

    return render_template("index.html", error=None)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))