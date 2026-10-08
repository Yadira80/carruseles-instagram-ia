#!/usr/bin/env python3
"""Página de resultados de los carruseles, con botón "Publicar en Instagram".

Uso:
    python servidor.py [--raiz CARPETA_CARRUSELES] [--puerto 8765]
Luego abre http://localhost:8765

Solo escucha en 127.0.0.1. La clave de Upload-Post se queda en este proceso (nunca llega
al navegador). Las acciones que escriben exigen la cabecera X-Carrusel, que una web ajena
no puede enviar a localhost sin CORS, y se comprueba la cabecera Host.
"""
import argparse
import json
import mimetypes
import sys
import threading
import traceback
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upload_post  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
PAGE = SKILL_DIR / "assets" / "web" / "resultados.html"
DEFAULT_ROOT = Path.home() / "OneDrive" / "Documentos" / "Carrusel Skill" / "carruseles"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

JOBS = {}  # slug -> {"activo": bool, "mensajes": [..]}
JOBS_LOCK = threading.Lock()


def carousel_dir(root, slug):
    if not slug or "/" in slug or "\\" in slug or slug.startswith("."):
        return None
    d = (root / slug).resolve()
    if d.parent != root.resolve() or not (d / "slides").is_dir():
        return None
    return d


def carousel_summary(root, d):
    titulo = d.name
    cj = d / "carrusel.json"
    if cj.exists():
        try:
            titulo = json.loads(cj.read_text(encoding="utf-8")).get("titulo") or titulo
        except ValueError:
            pass
    slides = upload_post.slide_files(d)
    pub = upload_post.read_estado(d)
    return {
        "slug": d.name,
        "titulo": titulo,
        "fecha": d.name[:10] if d.name[:4].isdigit() else "",
        "slides": [f"/archivos/{urllib.parse.quote(d.name)}/slides/{urllib.parse.quote(f.name)}" for f in slides],
        "caption": upload_post.read_caption(d),
        "publicacion": {k: pub.get(k) for k in ("estado", "url", "error", "publicado", "perfil", "actualizado")},
        "modificado": max((f.stat().st_mtime for f in slides), default=d.stat().st_mtime),
    }


def run_publish(folder, slug, caption, forzar):
    def progress(msg):
        with JOBS_LOCK:
            JOBS[slug]["mensajes"].append({"t": datetime.now().strftime("%H:%M:%S"), "m": msg})
        print(f"[{slug}] {msg}", flush=True)

    try:
        res = upload_post.publicar(folder, caption=caption, forzar=forzar, progress=progress)
        if res.get("estado") == "publicado":
            progress("¡Publicado en Instagram!")
        else:
            progress("Error: " + str(res.get("error")))
    except upload_post.UploadPostError as e:
        if upload_post.read_estado(folder).get("estado") != "publicado":
            upload_post.write_estado(folder, estado="error", error=str(e))
        progress("Error: " + str(e))
        with JOBS_LOCK:
            JOBS[slug]["error"] = str(e)
    except Exception as e:  # noqa: BLE001 - cualquier fallo debe llegar a la página
        traceback.print_exc()
        upload_post.write_estado(folder, estado="error", error=f"Error inesperado: {e}")
        progress(f"Error inesperado: {e}")
        with JOBS_LOCK:
            JOBS[slug]["error"] = str(e)
    finally:
        with JOBS_LOCK:
            JOBS[slug]["activo"] = False


class Handler(BaseHTTPRequestHandler):
    server_version = "CarruselIG/1.0"

    def log_message(self, fmt, *args):  # silenciamos el log de cada petición
        pass

    # ------------------------------------------------------------- utilidades
    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        return host in (f"localhost:{port}", f"127.0.0.1:{port}")

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("cuerpo demasiado grande")
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def _send_file(self, path):
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------- GET
    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"error": "host no permitido"})
        root = self.server.root
        parts = [urllib.parse.unquote(p) for p in urllib.parse.urlparse(self.path).path.strip("/").split("/") if p]

        if not parts or parts[0] == "c":
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return self.wfile.write(body)

        if parts[0] == "archivos" and len(parts) >= 3:
            d = carousel_dir(root, parts[1])
            if d:
                f = (d / "/".join(parts[2:])).resolve()
                if d in f.parents and f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".pdf"):
                    return self._send_file(f)
            return self._json(404, {"error": "archivo no encontrado"})

        if parts[:2] == ["api", "carruseles"]:
            items = [carousel_summary(root, d) for d in root.iterdir() if carousel_dir(root, d.name)] \
                if root.exists() else []
            items.sort(key=lambda x: x["modificado"], reverse=True)
            return self._json(200, {"carruseles": items})

        if parts[:2] == ["api", "config"]:
            key, user = upload_post.load_config()
            return self._json(200, {"clave": bool(key), "perfil": user or None,
                                    "archivo": str(upload_post.ENV_FILE)})

        if parts[0] == "api" and len(parts) >= 3 and parts[1] == "c":
            d = carousel_dir(root, parts[2])
            if not d:
                return self._json(404, {"error": "carrusel no encontrado"})
            if len(parts) == 3:
                return self._json(200, carousel_summary(root, d))
            if parts[3] == "publicacion":
                with JOBS_LOCK:
                    job = dict(JOBS.get(d.name) or {"activo": False, "mensajes": []})
                    job["mensajes"] = list(job.get("mensajes", []))
                job["publicacion"] = carousel_summary(root, d)["publicacion"]
                return self._json(200, job)

        return self._json(404, {"error": "no encontrado"})

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Carrusel") != "1":
            return self._json(403, {"error": "petición no permitida"})
        root = self.server.root
        parts = [urllib.parse.unquote(p) for p in urllib.parse.urlparse(self.path).path.strip("/").split("/") if p]
        if len(parts) != 4 or parts[:2] != ["api", "c"]:
            return self._json(404, {"error": "no encontrado"})
        d = carousel_dir(root, parts[2])
        if not d:
            return self._json(404, {"error": "carrusel no encontrado"})
        try:
            body = self._body()
        except ValueError as e:
            return self._json(400, {"error": f"JSON no válido: {e}"})

        caption = str(body.get("caption", "")).replace("\r\n", "\n").strip()

        if parts[3] == "caption":
            (d / "caption.md").write_text(caption + "\n", encoding="utf-8")
            return self._json(200, {"ok": True})

        if parts[3] == "publicar":
            problems = upload_post.validate(upload_post.slide_files(d), caption)
            if problems:
                return self._json(400, {"error": "No se puede publicar: " + "; ".join(problems) + "."})
            with JOBS_LOCK:
                if JOBS.get(d.name, {}).get("activo"):
                    return self._json(409, {"error": "Ya se está publicando este carrusel."})
                JOBS[d.name] = {"activo": True, "mensajes": [], "error": None}
            # Guardamos el texto que se va a publicar
            (d / "caption.md").write_text(caption + "\n", encoding="utf-8")
            threading.Thread(target=run_publish, args=(d, d.name, caption, bool(body.get("forzar"))),
                             daemon=True).start()
            return self._json(202, {"ok": True})

        return self._json(404, {"error": "no encontrado"})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raiz", default=str(DEFAULT_ROOT), help="carpeta que contiene los carruseles")
    ap.add_argument("--puerto", type=int, default=8765)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.puerto), Handler)
    server.root = Path(args.raiz).resolve()
    print(f"Página de resultados: http://localhost:{args.puerto}  (carruseles en {server.root})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
