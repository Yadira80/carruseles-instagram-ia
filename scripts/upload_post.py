#!/usr/bin/env python3
"""Publica un carrusel en Instagram con la API de Upload-Post (https://upload-post.com).

Uso:
    python upload_post.py comprobar               # valida la clave y lista tus perfiles (no publica)
    python upload_post.py publicar CARPETA        # publica slides/ + caption.md en Instagram

Configuración (variables de entorno o archivo ~/.env-uploadpost):
    UPLOADPOST_API_KEY=tu_clave
    UPLOADPOST_USER=tu_perfil      # opcional si solo tienes un perfil con Instagram

Para no perder ni duplicar publicaciones:
  - La subida es asíncrona (async_upload=true) y se consulta el estado hasta que termina.
  - Cada publicación lleva una Idempotency-Key calculada a partir del perfil, el texto y las
    imágenes: si una petición se reintenta tras un corte, Upload-Post devuelve el trabajo
    existente en lugar de publicar otra vez.
  - El request_id se guarda en CARPETA/publicacion.json antes de esperar, para poder
    retomar la consulta si el proceso se corta.
Solo librería estándar (PyMuPDF opcional para convertir las slides a JPEG ligeras).
"""
import hashlib
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

API = "https://api.upload-post.com/api"
UA = "carrusel-ig-skill/1.0"
ENV_FILE = Path.home() / ".env-uploadpost"
MAX_CAPTION = 2200
MAX_HASHTAGS = 30
MAX_SLIDES = 20
JPEG_WIDTH = 1440  # ancho máximo que Instagram aprovecha

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


class UploadPostError(RuntimeError):
    pass


# ---------------------------------------------------------------- configuración


def load_config():
    cfg = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            k, _, v = line.partition("=")
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("UPLOADPOST_API_KEY", "UPLOADPOST_USER"):
        if os.environ.get(k, "").strip():
            cfg[k] = os.environ[k].strip()
    return cfg.get("UPLOADPOST_API_KEY", ""), cfg.get("UPLOADPOST_USER", "")


def require_key():
    key, user = load_config()
    if not key:
        raise UploadPostError(
            "Falta la clave de Upload-Post. Ábrela en https://app.upload-post.com (API Keys) y pégala en "
            f"{ENV_FILE} en la línea UPLOADPOST_API_KEY=")
    return key, user


# ------------------------------------------------------------------------ HTTP

HTTP_HINTS = {
    400: "petición no válida",
    401: "clave de Upload-Post inválida o caducada (revisa UPLOADPOST_API_KEY)",
    403: "tu plan de Upload-Post no permite esta acción",
    404: "no se encuentra el perfil o la publicación (revisa UPLOADPOST_USER)",
    429: "has llegado al límite mensual de publicaciones de tu plan de Upload-Post",
}


def _message(payload):
    if not isinstance(payload, dict):
        return str(payload)[:300]
    return str(payload.get("message") or payload.get("error") or payload.get("detail") or payload)[:500]


def _request(method, path, key, data=None, headers=None, timeout=60):
    url = path if path.startswith("http") else API + path
    h = {"Authorization": f"Apikey {key}", "User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = raw
        hint = HTTP_HINTS.get(e.code, "error del servidor de Upload-Post" if e.code >= 500 else "")
        err = UploadPostError(f"Upload-Post {e.code}: {_message(payload)}" + (f" — {hint}" if hint else ""))
        err.status = e.code
        err.payload = payload
        raise err
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        err = UploadPostError(f"No se pudo conectar con Upload-Post: {e}")
        err.status = None
        err.network = True
        raise err
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        raise UploadPostError(f"Respuesta inesperada de Upload-Post: {raw[:200]}")
    payload.setdefault("_http_status", status)
    return payload


def _multipart(fields, files):
    boundary = "----carrusel" + uuid.uuid4().hex
    out = []
    for name, value in fields:
        out.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode()
                   + str(value).encode("utf-8") + b"\r\n")
    for name, filename, content, mime in files:
        out.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
                   f"Content-Type: {mime}\r\n\r\n".encode() + content + b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


# -------------------------------------------------------------------- perfiles


def list_profiles(key):
    data = _request("GET", "/uploadposts/users", key)
    profiles = []
    for p in data.get("profiles") or []:
        ig = (p.get("social_accounts") or {}).get("instagram")
        profiles.append({
            "username": p.get("username"),
            "instagram": (ig.get("handle") or ig.get("display_name") or "conectado") if isinstance(ig, dict) and ig else None,
        })
    return profiles


def resolve_user(key, user):
    if user:
        return user
    con_ig = [p for p in list_profiles(key) if p["instagram"]]
    if len(con_ig) == 1:
        return con_ig[0]["username"]
    if not con_ig:
        raise UploadPostError("Ningún perfil de Upload-Post tiene Instagram conectado. Conéctalo en "
                              "https://app.upload-post.com/manage-users")
    nombres = ", ".join(p["username"] for p in con_ig)
    raise UploadPostError(f"Tienes varios perfiles con Instagram ({nombres}). Escribe el que quieras en "
                          f"{ENV_FILE} en la línea UPLOADPOST_USER=")


# ---------------------------------------------------------------- contenido


def slide_files(folder):
    files = sorted(p for p in (Path(folder) / "slides").glob("slide-*.*")
                   if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    return files


def read_caption(folder):
    p = Path(folder) / "caption.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def validate(files, caption):
    problems = []
    if len(files) < 2:
        problems.append("un carrusel necesita al menos 2 slides en la carpeta slides/")
    if len(files) > MAX_SLIDES:
        problems.append(f"Instagram admite como máximo {MAX_SLIDES} imágenes por carrusel ({len(files)})")
    if not caption:
        problems.append("falta el texto del post (caption.md está vacío)")
    if len(caption) > MAX_CAPTION:
        problems.append(f"el texto tiene {len(caption)} caracteres; Instagram admite {MAX_CAPTION}")
    tags = re.findall(r"(?<!\w)#\w+", caption)
    if len(tags) > MAX_HASHTAGS:
        problems.append(f"el texto tiene {len(tags)} hashtags; Instagram admite {MAX_HASHTAGS}")
    return problems


def prepare_images(folder, files):
    """Convierte las slides a JPEG de 1440 px (Instagram no necesita más y la subida es 5–10× más ligera)."""
    out = []
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # PyMuPDF antiguo
        except ImportError:
            fitz = None
    cache = Path(folder) / "_publicar"
    cache.mkdir(exist_ok=True)
    for f in files:
        if fitz is None:
            mime = "image/png" if f.suffix.lower() == ".png" else "image/jpeg"
            out.append((f.name, f.read_bytes(), mime))
            continue
        dest = cache / (f.stem + ".jpg")
        if not dest.exists() or dest.stat().st_mtime < f.stat().st_mtime:
            pix = fitz.Pixmap(str(f))
            if pix.alpha:
                pix = fitz.Pixmap(pix, 0)
            if pix.width > JPEG_WIDTH:
                pix = fitz.Pixmap(pix, JPEG_WIDTH, round(pix.height * JPEG_WIDTH / pix.width))
            dest.write_bytes(pix.tobytes("jpeg", jpg_quality=92))
        out.append((dest.name, dest.read_bytes(), "image/jpeg"))
    return out


# ------------------------------------------------------------------ publicar


def _estado_path(folder):
    return Path(folder) / "publicacion.json"


def read_estado(folder):
    p = _estado_path(folder)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def write_estado(folder, **kw):
    data = read_estado(folder)
    data.update(kw)
    data["actualizado"] = datetime.now().isoformat(timespec="seconds")
    tmp = _estado_path(folder).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_estado_path(folder))
    return data


def _find_url(obj):
    """Busca la URL del post en una respuesta (la forma exacta varía según el endpoint)."""
    if isinstance(obj, dict):
        for k in ("url", "post_url", "permalink"):
            v = obj.get(k)
            if isinstance(v, str) and v.startswith("http"):
                return v
        for v in obj.values():
            u = _find_url(v)
            if u:
                return u
    elif isinstance(obj, list):
        for v in obj:
            u = _find_url(v)
            if u:
                return u
    return None


def _instagram_result(payload):
    """Devuelve (terminado, ok, url, mensaje) a partir de una respuesta síncrona o de estado."""
    results = payload.get("results")
    ig = None
    if isinstance(results, dict):
        ig = results.get("instagram")
    elif isinstance(results, list):
        ig = next((r for r in results if str(r.get("platform", "")).lower() == "instagram"), None)
    status = str(payload.get("status", "")).lower()

    if ig is not None:
        ig_status = str(ig.get("status", "")).lower()
        if ig.get("skipped"):
            return True, False, None, "El perfil de Upload-Post no tiene Instagram conectado."
        if ig.get("success") is True or ig_status == "completed":
            return True, True, _find_url(ig) or _find_url(payload), ig.get("message") or "Publicado"
        if ig.get("success") is False and ig_status not in ("queued", "processing", "retryable", "in_progress"):
            return True, False, None, str(ig.get("error") or ig.get("message") or "Instagram rechazó la publicación")
    if status == "failed":
        return True, False, None, _message(payload)
    if status == "completed":
        if ig is None:
            return True, False, None, "Upload-Post terminó sin resultado para Instagram: " + _message(payload)
    return False, None, None, payload.get("message") or status or "en proceso"


def poll_status(key, request_id, folder, progress, timeout=900):
    start, delay, net_errors = time.time(), 4, 0
    while time.time() - start < timeout:
        time.sleep(delay)
        try:
            payload = _request("GET", "/uploadposts/status?" + urllib.parse.urlencode({"request_id": request_id}),
                               key, timeout=60)
        except UploadPostError as e:
            if getattr(e, "network", False) or (getattr(e, "status", None) or 0) >= 500:
                net_errors += 1
                progress(f"Reintentando consultar el estado ({net_errors})…")
                if net_errors > 30:
                    raise
                delay = min(delay * 1.5, 20)
                continue
            if getattr(e, "status", None) == 404:
                progress("Upload-Post aún no tiene el estado, sigo esperando…")
                continue
            raise
        net_errors = 0
        done, ok, url, msg = _instagram_result(payload)
        if done:
            return ok, url, msg, payload
        progress(f"Instagram está procesando el carrusel ({payload.get('completed', 0)}/{payload.get('total', 1)})…")
        delay = min(delay + 2, 10)
    raise UploadPostError(f"La publicación sigue en proceso tras {timeout // 60} min. Revisa el historial en "
                          "https://app.upload-post.com (request_id guardado en publicacion.json).")


def publicar(folder, caption=None, forzar=False, progress=print):
    folder = Path(folder)
    key, user = require_key()
    files = slide_files(folder)
    caption = (caption if caption is not None else read_caption(folder)).strip()
    problems = validate(files, caption)
    if problems:
        raise UploadPostError("No se puede publicar: " + "; ".join(problems) + ".")

    prev = read_estado(folder)
    if prev.get("estado") == "publicado" and not forzar:
        raise UploadPostError("Este carrusel ya está publicado. Usa 'publicar de nuevo' si de verdad quieres duplicarlo.")

    progress("Comprobando tu perfil de Upload-Post…")
    user = resolve_user(key, user)
    progress(f"Preparando {len(files)} slides…")
    images = prepare_images(folder, files)

    h = hashlib.sha256()
    h.update(user.encode())
    h.update(caption.encode("utf-8"))
    for _, content, _ in images:
        h.update(hashlib.sha256(content).digest())
    # Tras un rechazo definitivo de Instagram, el reintento debe ser una petición nueva
    h.update(str(prev.get("fallos_definitivos", 0)).encode())
    if forzar:
        h.update(str(time.time()).encode())
    idem = "carrusel-" + h.hexdigest()[:40]

    # Reanudar si ya hay una subida en curso con exactamente el mismo contenido
    if prev.get("estado") == "en_proceso" and prev.get("idempotency_key") == idem and prev.get("request_id"):
        progress("Retomando la publicación que estaba en curso…")
        request_id = prev["request_id"]
    else:
        request_id = "carrusel-" + uuid.uuid4().hex[:24]
        fields = [("user", user), ("platform[]", "instagram"), ("title", caption),
                  ("async_upload", "true"), ("request_id", request_id), ("external_id", folder.name[:255])]
        body, ctype = _multipart(fields, [("photos[]", n, c, m) for n, c, m in images])
        write_estado(folder, estado="en_proceso", request_id=request_id, idempotency_key=idem, perfil=user,
                     slides=len(images), inicio=datetime.now().isoformat(timespec="seconds"), error=None, url=None)
        progress(f"Subiendo {len(images)} imágenes a Upload-Post ({len(body) // 1024 // 1024 + 1} MB)…")
        payload = None
        for attempt in range(4):
            try:
                payload = _request("POST", "/upload_photos", key, data=body, timeout=300, headers={
                    "Content-Type": ctype, "Idempotency-Key": idem, "X-Request-Id": request_id})
                break
            except UploadPostError as e:
                retry = getattr(e, "network", False) or (getattr(e, "status", None) or 0) >= 500
                if not retry or attempt == 3:
                    write_estado(folder, estado="error", error=str(e))
                    raise
                progress(f"Fallo de conexión, reintento {attempt + 1}/3 (sin riesgo de duplicar)…")
                time.sleep(5 * (attempt + 1))
        request_id = payload.get("request_id") or request_id
        write_estado(folder, request_id=request_id)
        done, ok, url, msg = _instagram_result(payload)
        if done:
            return _finish(folder, ok, url, msg)

    ok, url, msg, _ = poll_status(key, request_id, folder, progress)
    return _finish(folder, ok, url, msg)


def _finish(folder, ok, url, msg):
    if ok:
        data = write_estado(folder, estado="publicado", url=url, mensaje=msg, error=None,
                            publicado=datetime.now().isoformat(timespec="seconds"))
    else:
        fallos = read_estado(folder).get("fallos_definitivos", 0) + 1
        data = write_estado(folder, estado="error", error=msg, fallos_definitivos=fallos)
    return data


# ------------------------------------------------------------------------ CLI


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("comprobar", help="valida la clave y lista los perfiles (no publica nada)")
    p = sub.add_parser("publicar", help="publica CARPETA en Instagram")
    p.add_argument("carpeta")
    p.add_argument("--forzar", action="store_true", help="publica aunque ya conste como publicado")
    args = ap.parse_args()
    try:
        if args.cmd == "comprobar":
            key, user = require_key()
            profiles = list_profiles(key)
            print("Clave válida. Perfiles de Upload-Post:")
            for pr in profiles:
                print(f"  - {pr['username']}: Instagram {pr['instagram'] or 'NO conectado'}")
            print(f"Perfil que se usará: {resolve_user(key, user)}")
        else:
            r = publicar(args.carpeta, forzar=args.forzar, progress=lambda m: print(m, flush=True))
            if r.get("estado") == "publicado":
                print(f"¡Publicado en Instagram! {r.get('url') or ''}")
            else:
                print(f"ERROR: {r.get('error')}", file=sys.stderr)
                sys.exit(1)
    except UploadPostError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
