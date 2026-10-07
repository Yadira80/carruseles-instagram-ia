#!/usr/bin/env python3
"""Genera carruseles de Instagram con Nano Banana Pro (Kie.ai).

Subcomandos:
    python kie_carrusel.py generar CARPETA [--solo 3,5] [--timeout 1200]
    python kie_carrusel.py prompts CARPETA          # solo escribe prompts.md (gratis)
    python kie_carrusel.py avatar --fotos a.jpg b.jpg c.jpg [--opciones 2]
    python kie_carrusel.py creditos

CARPETA contiene carrusel.json (lo escribe el agente). El script:
  1. Sube a Kie.ai las imágenes de referencia (avatar de marca) para tener URL pública.
  2. Genera la PORTADA primero y espera a que termine.
  3. Genera el resto de slides en paralelo, pasando avatar + portada como referencia
     para que todas compartan estilo, paleta, tipografía y personaje.
  4. Descarga todo a CARPETA/slides/, y crea vista-previa.jpg y carrusel.pdf.

Pensado para no perder nada por timeouts:
  - Cada taskId se guarda en CARPETA/estado.json en cuanto Kie.ai lo devuelve.
  - Si el proceso se corta, volver a lanzar el mismo comando REANUDA las tareas
    pendientes (no crea otras ni gasta créditos de nuevo) y salta las slides ya hechas.
  - Errores de red, 429 y 5xx se reintentan con espera creciente; un fallo puntual
    al consultar el estado no mata la tarea.

La clave se lee de KIE_API_KEY o de ~/.env-kie. Solo librería estándar
(PyMuPDF opcional para vista previa y PDF).
"""
import argparse
import base64
import hashlib
import http.client
import json
import math
import os
import re
import socket
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

API = "https://api.kie.ai/api/v1"
UPLOAD_API = "https://kieai.redpandaai.co/api/file-stream-upload"
DEFAULT_MODEL = "nano-banana-pro"
UA = "carrusel-ig-skill/1.0"
SKILL_DIR = Path(__file__).resolve().parent.parent
ASSETS = SKILL_DIR / "assets"
UPLOAD_TTL = 20 * 3600  # Kie borra las subidas a las 24 h; re-subimos antes

FATAL_CODES = {400, 401, 402, 404, 422}

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

_print_lock = threading.Lock()


def log(msg, err=False):
    with _print_lock:
        print(f"{datetime.now():%H:%M:%S} {msg}", file=sys.stderr if err else sys.stdout, flush=True)


# --------------------------------------------------------------------------- API


class KieError(RuntimeError):
    def __init__(self, msg, code=None, retryable=False):
        super().__init__(msg)
        self.code = code
        self.retryable = retryable


def load_key():
    key = os.environ.get("KIE_API_KEY", "").strip()
    if key:
        return key
    env_file = Path.home() / ".env-kie"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("KIE_API_KEY"):
                val = line.partition("=")[2].strip().strip('"').strip("'")
                if val:
                    return val
    sys.exit("ERROR: no encuentro la clave de Kie.ai. Crea ~/.env-kie con: KIE_API_KEY=tu_clave "
             "(https://kie.ai/api-key)")


HINTS = {
    401: "clave inválida o sin permiso para este modelo (https://kie.ai/api-key → Model Access → All Models)",
    402: "créditos insuficientes en Kie.ai (recarga en https://kie.ai/pricing)",
    422: "parámetros no válidos",
    429: "demasiadas peticiones",
}


def _parse_payload(payload):
    code = payload.get("code")
    if code in (200, None):
        return payload.get("data")
    msg = f"Kie.ai {code}: {payload.get('msg')}"
    if code in HINTS:
        msg += f" — {HINTS[code]}"
    retryable = code not in FATAL_CODES  # 429, 455, 5xx… se pueden reintentar
    raise KieError(msg, code, retryable)


def _open(req, timeout):
    """urlopen que convierte cualquier fallo en KieError (retryable o no)."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.headers
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return _parse_payload(json.loads(body)), None  # payload con code de Kie
        except KieError:
            raise
        except ValueError:
            raise KieError(f"HTTP {e.code}: {body[:200]}", e.code,
                           retryable=e.code == 429 or e.code >= 500)
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
            http.client.HTTPException, OSError) as e:
        raise KieError(f"error de red: {e}", None, retryable=True)


def call(method, path, key, body=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": UA})
    raw, headers = _open(req, timeout)
    if headers is None:  # ya parseado desde HTTPError
        return raw
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise KieError(f"respuesta no JSON: {raw[:200]!r}", None, retryable=True)
    return _parse_payload(payload)


def with_retries(fn, what, attempts=6, base=4):
    """Reintenta fn() ante errores transitorios con espera exponencial."""
    for i in range(attempts):
        try:
            return fn()
        except KieError as e:
            if not e.retryable or i == attempts - 1:
                raise
            wait = min(base * 2 ** i, 60)
            log(f"  {what}: {e} → reintento en {wait}s", err=True)
            time.sleep(wait)


def upload_file(path, key):
    """Sube un archivo local a Kie.ai (multipart) y devuelve su URL pública (24 h)."""
    path = Path(path)
    content = path.read_bytes()
    mime = "image/png"
    if path.suffix.lower() in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif path.suffix.lower() == ".webp":
        mime = "image/webp"
    # Las slides en 2K pesan; las pasamos a JPEG para que la subida sea ligera.
    if len(content) > 3_000_000:
        try:
            import fitz  # PyMuPDF
            content = fitz.Pixmap(str(path)).tobytes("jpeg", jpg_quality=90)
            mime = "image/jpeg"
        except Exception:
            pass
    ext = ".jpg" if mime == "image/jpeg" else (".webp" if mime == "image/webp" else ".png")
    # Nombre seguro para URL: sin espacios, tildes ni símbolos (si no, Kie.ai no puede leerla)
    safe = unicodedata.normalize("NFKD", path.stem).encode("ascii", "ignore").decode()
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", safe).strip("-").lower()[:40] or "img"
    fname = f"{safe}-{uuid.uuid4().hex[:8]}{ext}"
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in (("uploadPath", "carrusel-ig"), ("fileName", fname)):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\n"
                 f"Content-Type: {mime}\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    def do():
        req = urllib.request.Request(UPLOAD_API, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "User-Agent": UA,
            "Content-Type": f"multipart/form-data; boundary={boundary}"})
        raw, headers = _open(req, 180)
        data = raw if headers is None else _parse_payload(json.loads(raw.decode("utf-8")))
        url = (data or {}).get("downloadUrl") or (data or {}).get("fileUrl")
        if not url:
            raise KieError(f"la subida no devolvió URL: {data}", None, retryable=True)
        # Comprobamos que la URL es legible antes de gastar créditos con ella
        check = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-1023"})
        _, h = _open(check, 60)
        if h is None:
            raise KieError(f"la imagen subida no es accesible: {url}", None, retryable=True)
        return url

    return with_retries(do, f"subida {path.name}")


def download(url, dest_base):
    def do():
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        raw, headers = _open(req, 180)
        if headers is None:
            raise KieError("la descarga devolvió un error", None, retryable=True)
        return raw, headers.get("Content-Type", "")

    content, ctype = with_retries(do, f"descarga {dest_base.name}", attempts=5)
    low = url.lower().split("?")[0]
    ext = ".png"
    if "jpeg" in ctype or "jpg" in ctype or low.endswith((".jpg", ".jpeg")):
        ext = ".jpg"
    elif "webp" in ctype or low.endswith(".webp"):
        ext = ".webp"
    for old in dest_base.parent.glob(dest_base.name + ".*"):
        old.unlink()
    dest = dest_base.with_suffix(ext)
    dest.write_bytes(content)
    return dest


def create_task(key, model, inp):
    data = with_retries(lambda: call("POST", "/jobs/createTask", key, {"model": model, "input": inp}),
                        "createTask")
    task_id = (data or {}).get("taskId")
    if not task_id:
        raise KieError(f"createTask no devolvió taskId: {data}", None, retryable=False)
    return task_id


def wait_task(key, task_id, label, timeout):
    """Espera a que la tarea termine. Tolera errores puntuales al consultar."""
    start = time.time()
    delay, errors, last_state, last_note = 5, 0, None, start
    while time.time() - start < timeout:
        time.sleep(delay)
        try:
            info = call("GET", f"/jobs/recordInfo?taskId={task_id}", key) or {}
        except KieError as e:
            if not e.retryable:
                raise
            errors += 1
            log(f"[{label}] consulta con error ({errors}): {e}", err=True)
            if errors >= 40:
                raise TimeoutError(f"{label}: demasiados errores seguidos consultando el estado "
                                   f"(taskId {task_id}). Relanza el comando para reanudar.")
            delay = min(delay * 1.5, 30)
            continue
        errors = 0
        state = info.get("state")
        if state == "success":
            result = json.loads(info.get("resultJson") or "{}")
            urls = result.get("resultUrls") or []
            if not urls:
                raise KieError("la tarea terminó sin URL de imagen", None, retryable=True)
            return urls[0], info
        if state == "fail":
            raise KieError(f"Kie.ai marcó la tarea como fallida: {info.get('failCode')} {info.get('failMsg')}",
                           None, retryable=True)
        if state != last_state or time.time() - last_note > 60:
            log(f"[{label}] estado: {state} ({int(time.time() - start)}s)")
            last_state, last_note = state, time.time()
        delay = min(delay + 2, 12)
    raise TimeoutError(f"{label}: sigue en proceso tras {timeout}s (taskId {task_id}). "
                       "Vuelve a lanzar el comando para seguir esperando sin pagar de nuevo.")


# --------------------------------------------------------------------- estado


class Estado:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"slides": {}, "uploads": {}}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except ValueError:
                pass

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def slide(self, n):
        return self.data["slides"].setdefault(str(n), {})

    def update_slide(self, n, **kw):
        with self.lock:
            self.slide(n).update(kw)
            self.save()

    def upload_url(self, path, key):
        path = Path(path).resolve()
        st = path.stat()
        ident = f"{path}|{int(st.st_mtime)}|{st.st_size}"
        with self.lock:
            hit = self.data["uploads"].get(ident)
        if hit and time.time() - hit["t"] < UPLOAD_TTL:
            return hit["url"]
        log(f"Subiendo referencia {path.name}…")
        url = upload_file(path, key)
        with self.lock:
            self.data["uploads"][ident] = {"url": url, "t": time.time()}
            self.save()
        return url


# ------------------------------------------------------------------- prompts


def load_style(name):
    p = ASSETS / "estilos" / f"{name}.json"
    if not p.exists():
        disponibles = ", ".join(x.stem for x in (ASSETS / "estilos").glob("*.json"))
        sys.exit(f"ERROR: no existe el estilo '{name}'. Disponibles: {disponibles}")
    return json.loads(p.read_text(encoding="utf-8"))


def resolve_ref(ref, base):
    if str(ref).startswith(("http://", "https://")):
        return str(ref)
    for root in (base, ASSETS):
        p = (root / ref)
        if p.exists():
            return p
    sys.exit(f"ERROR: no encuentro la imagen de referencia '{ref}'")


def text_lines(slide, n, total, cuenta, style):
    lines = []
    labels = style["etiquetas_texto"]
    for field in ("antetitulo", "titulo", "subtitulo", "texto"):
        if slide.get(field):
            lines.append(f'- {labels[field]}: "{slide[field]}"')
    for i, item in enumerate(slide.get("puntos") or [], 1):
        lines.append(f'- {labels["puntos"]} {i}: "{item}"')
    if slide.get("numero"):
        lines.append(f'- {labels["numero"]}: "{slide["numero"]}"')
    for b in slide.get("botones") or []:
        lines.append(f'- {labels["botones"]}: "{b}"')
    for extra in slide.get("etiquetas") or []:
        lines.append(f'- {labels["etiquetas"]}: "{extra}"')
    if slide["tipo"] == "portada" and style.get("texto_portada_fijo"):
        lines.append(f'- {labels["deslizar"]}: "{style["texto_portada_fijo"]}"')
    if cuenta:
        lines.append(f'- {labels["cuenta"]}: "{cuenta}"')
    lines.append(f'- {labels["contador"]}: "{n}/{total}"')
    return lines


def build_prompt(c, style, n, total, slide, refs_desc):
    tipo = slide["tipo"]
    cuenta = c.get("cuenta", style.get("cuenta", ""))
    parts = [
        f"INSTAGRAM CAROUSEL — SLIDE {n} OF {total} — TYPE: {tipo.upper()}.",
        f"Vertical {c.get('formato', '4:5')} social media slide. Carousel topic: \"{c['titulo']}\". "
        f"Every slide of this carousel must look like part of ONE consistent, professionally designed set.",
    ]
    if refs_desc:
        parts.append("REFERENCE IMAGES:\n" + "\n".join(refs_desc))
    parts.append("ART STYLE (identical on every slide):\n" + style["estilo_global"])
    if c.get("_avatar") and style.get("personaje"):
        parts.append("MAIN CHARACTER:\n" + style["personaje"])
    parts.append("LAYOUT:\n" + style["layouts"][tipo] + "\n" + style["layout_comun"])
    parts.append("SCENE / ILLUSTRATION FOR THIS SLIDE:\n" + slide["escena"])
    parts.append(style["reglas_texto"] + "\n" + "\n".join(text_lines(slide, n, total, cuenta, style)))
    parts.append("TYPOGRAPHY:\n" + style["tipografia"])
    if slide.get("prompt_extra"):
        parts.append("ADDITIONAL NOTES:\n" + slide["prompt_extra"])
    parts.append("AVOID:\n" + style["evitar"])
    return "\n\n".join(parts)


def plan(folder):
    c = json.loads((folder / "carrusel.json").read_text(encoding="utf-8"))
    style = load_style(c.get("estilo", "rosa-palo-animado"))
    slides = c["slides"]
    total = len(slides)
    if total < 2 or total > 20:
        sys.exit("ERROR: un carrusel necesita entre 2 y 20 slides")
    if slides[0]["tipo"] != "portada" or slides[-1]["tipo"] != "cta":
        sys.exit("ERROR: la primera slide debe ser tipo 'portada' y la última tipo 'cta'")
    for i, s in enumerate(slides, 1):
        if s["tipo"] not in style["layouts"]:
            sys.exit(f"ERROR: slide {i}: tipo '{s['tipo']}' no existe en el estilo")
        if not s.get("escena"):
            sys.exit(f"ERROR: slide {i}: falta 'escena'")
        words = sum(len(str(s.get(f, "")).split()) for f in ("antetitulo", "titulo", "subtitulo", "texto"))
        words += sum(len(str(p).split()) for p in s.get("puntos") or [])
        if words > style.get("max_palabras", 45):
            log(f"AVISO slide {i}: {words} palabras; con más de {style.get('max_palabras', 45)} "
                "el texto suele salir peor. Considera acortarlo.", err=True)

    use_avatar = c.get("avatar", True) and style.get("avatar")
    if use_avatar and not (ASSETS / style["avatar"]).exists():
        sys.exit(f"ERROR: falta el avatar maestro ({ASSETS / style['avatar']}). "
                 "Créalo con el subcomando 'avatar'.")
    avatar_path = (ASSETS / style["avatar"]) if use_avatar else None
    c["_avatar"] = bool(use_avatar)

    items = []
    for n, s in enumerate(slides, 1):
        refs, desc = [], []
        if avatar_path:
            refs.append(("avatar", avatar_path))
            desc.append(f"- Image {len(refs)}: {style['ref_avatar']}")
        if n > 1:
            refs.append(("portada", "@portada"))
            desc.append(f"- Image {len(refs)}: {style['ref_portada']}")
        for r in s.get("referencias") or []:
            refs.append(("extra", resolve_ref(r, folder)))
            desc.append(f"- Image {len(refs)}: additional reference supplied for this slide "
                        f"({s.get('referencias_desc', 'use it as indicated in the scene description')}).")
        prompt = build_prompt(c, style, n, total, s, desc)
        if len(prompt) > 19000:
            sys.exit(f"ERROR: el prompt de la slide {n} supera el límite de 20.000 caracteres")
        items.append({"n": n, "tipo": s["tipo"], "prompt": prompt, "refs": refs,
                      "aspect_ratio": c.get("formato", "4:5"),
                      "resolution": c.get("resolucion", "2K"),
                      "output_format": c.get("output_format", "png")})
    return c, style, items


def write_prompts_md(folder, c, items):
    out = [f"# {c['titulo']}\n"]
    for it in items:
        refs = ", ".join(r[0] for r in it["refs"]) or "ninguna"
        out.append(f"## Slide {it['n']} — {it['tipo']}\n\nReferencias: {refs} · "
                   f"{it['aspect_ratio']} · {it['resolution']}\n\n```text\n{it['prompt']}\n```\n")
    (folder / "prompts.md").write_text("\n".join(out), encoding="utf-8")


def item_hash(it, model):
    h = hashlib.sha256()
    h.update(json.dumps([it["prompt"], it["aspect_ratio"], it["resolution"], it["output_format"], model,
                         [str(r[1]) for r in it["refs"]]], ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:16]


# ------------------------------------------------------------------- generar


def generate_one(it, ctx):
    n, key, estado, model = it["n"], ctx["key"], ctx["estado"], ctx["model"]
    label = f"slide {n}"
    h = item_hash(it, model)
    st = estado.slide(n)
    slides_dir = ctx["folder"] / "slides"

    if st.get("hash") == h and st.get("estado") == "ok" and st.get("archivo") and Path(st["archivo"]).exists():
        log(f"[{label}] ya generada, la salto")
        return {"n": n, "ok": True, "archivo": st["archivo"], "saltada": True}

    attempts = ctx["retries"] + 1
    last = None
    for attempt in range(attempts):
        try:
            st = estado.slide(n)
            task_id = st.get("taskId") if st.get("hash") == h and st.get("estado") == "pendiente" else None
            if task_id:
                log(f"[{label}] reanudando tarea existente {task_id}")
            else:
                urls = []
                for kind, ref in it["refs"]:
                    if ref == "@portada":
                        urls.append(estado.upload_url(ctx["portada_file"](), key))
                    elif isinstance(ref, Path):
                        urls.append(estado.upload_url(ref, key))
                    else:
                        urls.append(ref)
                inp = {"prompt": it["prompt"], "aspect_ratio": it["aspect_ratio"],
                       "resolution": it["resolution"], "output_format": it["output_format"]}
                if urls:
                    inp["image_input"] = urls
                with ctx["create_lock"]:  # escalonamos las creaciones para evitar 429
                    task_id = create_task(key, model, inp)
                    time.sleep(1.5)
                estado.update_slide(n, hash=h, taskId=task_id, estado="pendiente", creada=time.time())
                log(f"[{label}] tarea creada {task_id}")
            url, info = wait_task(key, task_id, label, ctx["timeout"])
            dest = download(url, slides_dir / f"slide-{n:02d}")
            estado.update_slide(n, estado="ok", archivo=str(dest), url=url,
                                segundos=round((info.get("costTime") or 0) / 1000))
            log(f"[{label}] OK → {dest.name}")
            return {"n": n, "ok": True, "archivo": str(dest)}
        except TimeoutError as e:
            # La tarea sigue viva en Kie.ai: NO la descartamos, se reanuda en la próxima ejecución.
            log(f"[{label}] {e}", err=True)
            return {"n": n, "ok": False, "error": str(e), "pendiente": True}
        except KieError as e:
            last = str(e)
            estado.update_slide(n, estado="fallo", error=last)
            log(f"[{label}] intento {attempt + 1}/{attempts} falló: {last}", err=True)
            if not e.retryable:
                break
            time.sleep(5)
    return {"n": n, "ok": False, "error": last}


def make_preview(folder, files):
    try:
        import fitz
    except ImportError:
        log("PyMuPDF no instalado: sin vista previa ni PDF (pip install pymupdf)", err=True)
        return
    files = [Path(f) for f in files]
    # PDF: una página por slide (útil para LinkedIn o para revisar)
    pdf = fitz.open()
    for f in files:
        pix = fitz.Pixmap(str(f))
        page = pdf.new_page(width=pix.width * 0.5, height=pix.height * 0.5)
        page.insert_image(page.rect, filename=str(f))
    pdf.save(str(folder / "carrusel.pdf"), deflate=True)
    pdf.close()
    # Hoja de contactos para revisar todo de un vistazo
    cols = min(4, len(files))
    rows = math.ceil(len(files) / cols)
    tw, th, gap = 400, 500, 16
    sheet = fitz.open()
    page = sheet.new_page(width=cols * tw + (cols + 1) * gap, height=rows * th + (rows + 1) * gap)
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    for i, f in enumerate(files):
        r, cidx = divmod(i, cols)
        x, y = gap + cidx * (tw + gap), gap + r * (th + gap)
        page.insert_image(fitz.Rect(x, y, x + tw, y + th), filename=str(f), keep_proportion=True)
    page.get_pixmap(dpi=96).save(str(folder / "vista-previa.png"))
    sheet.close()


def cmd_generar(args):
    folder = Path(args.carpeta).resolve()
    if not (folder / "carrusel.json").exists():
        sys.exit(f"ERROR: no existe {folder / 'carrusel.json'}")
    key = load_key()
    c, style, items = plan(folder)
    write_prompts_md(folder, c, items)
    (folder / "slides").mkdir(exist_ok=True)
    estado = Estado(folder / "estado.json")
    model = args.modelo or c.get("modelo", DEFAULT_MODEL)

    if args.solo:
        for n in [int(x) for x in args.solo.split(",") if x.strip()]:
            estado.update_slide(n, estado="forzar", hash=None, taskId=None)
            log(f"slide {n}: se regenerará")

    try:
        log(f"Créditos antes: {call('GET', '/chat/credit', key)}")
    except KieError as e:
        if e.code in (401, 402):
            sys.exit(f"ERROR: {e}")
        log(f"(no pude leer los créditos: {e})", err=True)

    def portada_file():
        f = estado.slide(1).get("archivo")
        if not f or not Path(f).exists():
            raise KieError("la portada no está disponible como referencia", None, retryable=False)
        return Path(f)

    ctx = {"key": key, "estado": estado, "model": model, "folder": folder, "timeout": args.timeout,
           "retries": args.reintentos, "create_lock": threading.Lock(), "portada_file": portada_file}

    log(f"Modelo {model} · {len(items)} slides · {items[0]['aspect_ratio']} · {items[0]['resolution']}")
    log("Paso 1/2: portada (marca el estilo del resto)…")
    first = generate_one(items[0], ctx)
    results = [first]
    if not first["ok"]:
        log("La portada no está lista; no genero el resto para no romper la coherencia.", err=True)
    else:
        log(f"Paso 2/2: {len(items) - 1} slides en paralelo con la portada como referencia…")
        with ThreadPoolExecutor(max_workers=max(1, min(args.paralelo, len(items) - 1))) as pool:
            results += list(pool.map(lambda it: generate_one(it, ctx), items[1:]))

    ok = [r for r in results if r["ok"]]
    try:
        log(f"Créditos después: {call('GET', '/chat/credit', key)}")
    except KieError:
        pass
    if len(ok) == len(items):
        make_preview(folder, [r["archivo"] for r in sorted(ok, key=lambda r: r["n"])])
        log(f"LISTO: {len(ok)}/{len(items)} slides en {folder / 'slides'}")
        log(f"Vista previa: {folder / 'vista-previa.png'} · PDF: {folder / 'carrusel.pdf'}")
        sys.exit(0)
    log(f"INCOMPLETO: {len(ok)}/{len(items)} slides.", err=True)
    for r in results:
        if not r["ok"]:
            tag = "PENDIENTE (relanza para reanudar)" if r.get("pendiente") else "FALLÓ"
            log(f"  {tag} slide {r['n']}: {r['error']}", err=True)
    sys.exit(2 if any(r.get("pendiente") for r in results) else 1)


def cmd_prompts(args):
    folder = Path(args.carpeta).resolve()
    c, style, items = plan(folder)
    write_prompts_md(folder, c, items)
    print(f"Prompts escritos en {folder / 'prompts.md'} ({len(items)} slides)")


# -------------------------------------------------------------------- avatar


def cmd_avatar(args):
    key = load_key()
    style = load_style(args.estilo)
    out = (ASSETS / "marca") if not args.out else Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    estado = Estado(out / "_estado-avatar.json")
    urls = [estado.upload_url(Path(f), key) for f in args.fotos]
    prompt = style["prompt_avatar"]
    if args.notas:
        prompt += "\n\nADDITIONAL NOTES: " + args.notas
    model = args.modelo or DEFAULT_MODEL
    inp = {"prompt": prompt, "image_input": urls, "aspect_ratio": "3:4", "resolution": "2K",
           "output_format": "png"}

    def one(i):
        try:
            tid = create_task(key, model, inp)
            log(f"[opción {i}] tarea {tid}")
            url, _ = wait_task(key, tid, f"opción {i}", args.timeout)
            dest = download(url, out / f"avatar-opcion-{i}")
            log(f"[opción {i}] OK → {dest}")
            return dest
        except (KieError, TimeoutError) as e:
            log(f"[opción {i}] falló: {e}", err=True)
            return None

    with ThreadPoolExecutor(max_workers=args.opciones) as pool:
        files = [f for f in pool.map(one, range(1, args.opciones + 1)) if f]
    print(f"\n{len(files)}/{args.opciones} opciones de avatar en {out}")
    print("Elige una y cópiala como avatar-maestro.png en esa carpeta.")
    sys.exit(0 if files else 1)


def cmd_creditos(_args):
    print(f"Créditos disponibles en Kie.ai: {call('GET', '/chat/credit', load_key())}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generar", help="genera las slides de CARPETA/carrusel.json")
    g.add_argument("carpeta")
    g.add_argument("--solo", help="regenera solo estas slides, p. ej. 3,5")
    g.add_argument("--modelo", help=f"modelo de Kie.ai (por defecto {DEFAULT_MODEL})")
    g.add_argument("--timeout", type=int, default=1200, help="segundos máximos de espera por slide")
    g.add_argument("--reintentos", type=int, default=2, help="reintentos si Kie.ai marca la tarea como fallida")
    g.add_argument("--paralelo", type=int, default=6)
    g.set_defaults(func=cmd_generar)

    p = sub.add_parser("prompts", help="escribe prompts.md sin generar nada")
    p.add_argument("carpeta")
    p.set_defaults(func=cmd_prompts)

    a = sub.add_parser("avatar", help="crea opciones del avatar de marca a partir de fotos")
    a.add_argument("--fotos", nargs="+", required=True)
    a.add_argument("--opciones", type=int, default=2)
    a.add_argument("--estilo", default="rosa-palo-animado")
    a.add_argument("--notas", help="indicaciones extra para el avatar")
    a.add_argument("--out")
    a.add_argument("--modelo")
    a.add_argument("--timeout", type=int, default=1200)
    a.set_defaults(func=cmd_avatar)

    k = sub.add_parser("creditos", help="muestra el saldo de Kie.ai")
    k.set_defaults(func=cmd_creditos)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
