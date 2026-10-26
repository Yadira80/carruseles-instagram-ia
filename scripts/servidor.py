#!/usr/bin/env python3
"""Página de resultados de los carruseles, con botón "Publicar en Instagram".

Uso:
    python servidor.py [--raiz CARPETA1 CARPETA2 ...] [--puerto 8765]
Luego abre http://localhost:8765

Solo escucha en 127.0.0.1. La clave de Upload-Post se queda en este proceso (nunca llega
al navegador). Las acciones que escriben exigen la cabecera X-Carrusel, que una web ajena
no puede enviar a localhost sin CORS, y se comprueba la cabecera Host.
"""
import argparse
import json
import os
import shutil
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
PROJECT = Path.home() / "OneDrive" / "Documentos" / "Carrusel Skill"
# Cada carpeta es una colección que aparece en la página (IA, motivación…)
DEFAULT_ROOTS = [PROJECT / "carruseles", PROJECT / "Carruseles de motivación"]

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

JOBS = {}  # slug -> {"activo": bool, "mensajes": [..]}
JOBS_LOCK = threading.Lock()


def carousel_dir(roots, slug):
    if not slug or "/" in slug or "\\" in slug or slug.startswith("."):
        return None
    for root in roots:
        d = (root / slug).resolve()
        if d.parent == root.resolve() and (d / "slides").is_dir():
            return d
    return None


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
        "coleccion": d.parent.name,
        "titulo": titulo,
        "fecha": d.name[:10] if d.name[:4].isdigit() else "",
        "slides": [f"/archivos/{urllib.parse.quote(d.name)}/slides/{urllib.parse.quote(f.name)}" for f in slides],
        "caption": upload_post.read_caption(d),
        "publicacion": {k: pub.get(k) for k in ("estado", "url", "error", "publicado", "perfil", "actualizado", "fecha_programada")},
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

        if not parts or parts[0] in ("c", "clave"):
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
            items = [carousel_summary(r, d) for r in root if r.exists()
                     for d in r.iterdir() if carousel_dir([r], d.name)]
            items.sort(key=lambda x: (x["coleccion"], x["slug"]))
            return self._json(200, {"carruseles": items})

        if parts[:2] == ["api", "config"]:
            key, user = upload_post.load_config()
            valida = False
            if key:
                try:
                    upload_post.list_profiles(key)
                    valida = True
                except upload_post.UploadPostError:
                    valida = False
            return self._json(200, {"clave": valida, "hay_clave": bool(key), "perfil": user or None,
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

    def _guardar_clave(self):
        """Valida una clave nueva de Upload-Post y la guarda en ~/.env-uploadpost (nunca la devuelve)."""
        try:
            clave = str(self._body().get("clave", "")).strip()
        except ValueError:
            return self._json(400, {"error": "Petición no válida"})
        if len(clave) < 20 or any(c.isspace() for c in clave):
            return self._json(400, {"error": "Eso no parece una clave de Upload-Post. Cópiala entera desde API Keys."})
        try:
            perfiles = upload_post.list_profiles(clave)
        except upload_post.UploadPostError as e:
            return self._json(400, {"error": f"Upload-Post no acepta esa clave: {e}"})
        env = upload_post.ENV_FILE
        lines = env.read_text(encoding="utf-8-sig").splitlines() if env.exists() else []
        out, puesta = [], False
        for line in lines:
            if line.strip().startswith("UPLOADPOST_API_KEY"):
                out.append(f"UPLOADPOST_API_KEY={clave}")
                puesta = True
            else:
                out.append(line)
        if not puesta:
            out.append(f"UPLOADPOST_API_KEY={clave}")
        env.write_text("\n".join(out) + "\n", encoding="utf-8")
        return self._json(200, {"ok": True, "perfiles": perfiles})

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Carrusel") != "1":
            return self._json(403, {"error": "petición no permitida"})
        root = self.server.root
        parts = [urllib.parse.unquote(p) for p in urllib.parse.urlparse(self.path).path.strip("/").split("/") if p]
        if parts == ["api", "config", "clave"]:
            return self._guardar_clave()
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

        if parts[3] == "abrir-carpeta":
            # Publicación a mano: JPEG de 1440 px, numerados en orden, listos para subir a Instagram
            files = upload_post.slide_files(d)
            if not files:
                return self._json(400, {"error": "Este carrusel no tiene slides."})
            upload_post.prepare_images(d, files)
            # Copia en el Escritorio, en una carpeta fácil de encontrar desde Instagram
            desk = next((x for x in (Path.home() / "OneDrive" / "Desktop", Path.home() / "OneDrive" / "Escritorio",
                                     Path.home() / "Desktop") if x.is_dir()), d)
            dest = desk / "PUBLICAR HOY"
            dest.mkdir(exist_ok=True)
            for old in dest.glob("slide-*.jpg"):
                old.unlink()
            for f in sorted((d / "_publicar").glob("slide-*.jpg")):
                shutil.copy2(f, dest / f.name)
            try:
                os.startfile(str(dest))
            except (AttributeError, OSError) as e:
                return self._json(500, {"error": f"No pude abrir la carpeta: {e}", "ruta": str(dest)})
            return self._json(200, {"ok": True, "ruta": str(dest)})

        if parts[3] == "marcar-publicado":
            url = str(body.get("url") or "").strip() or None
            upload_post.write_estado(d, estado="publicado", url=url, error=None, mensaje="Publicado a mano",
                                     publicado=datetime.now().isoformat(timespec="seconds"))
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
    ap.add_argument("--raiz", nargs="+", default=[str(r) for r in DEFAULT_ROOTS],
                    help="carpetas que contienen carruseles (una por colección)")
    ap.add_argument("--puerto", type=int, default=8765)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.puerto), Handler)
    server.root = [Path(r).resolve() for r in args.raiz]
    for r in server.root:
        r.mkdir(parents=True, exist_ok=True)
    print(f"Página de resultados: http://localhost:{args.puerto}  (colecciones: "
          f"{', '.join(r.name for r in server.root)})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
