---
name: carrusel-ig
description: Crea carruseles de Instagram completos (portada + slides de contenido + call to action) con imágenes con texto generadas por Nano Banana Pro en Kie.ai, con estilo animado rosa palo y blanco y el avatar animado de Yadira, para su canal de inteligencia artificial @yadira.official. Úsala SIEMPRE que Yadira pida un carrusel, post de varias imágenes, slides para Instagram o "contenido para el feed" — sobre las noticias de IA de hoy, un tema, un guion, un texto, un artículo o un vídeo — aunque no diga "carrusel" ni "Kie" (p. ej. "hazme un post con las noticias de IA de hoy", "convierte este guion en slides", "saca un carrusel de este vídeo de YouTube", "prepárame contenido sobre ChatGPT para Instagram").
---

# Carruseles de Instagram con Nano Banana Pro

Cada carrusel es una historia: la **portada** engancha, las **slides del medio** cumplen la promesa de la portada (una idea por slide, en orden) y la **última** pide una acción. Todas comparten el mismo estilo, la misma tipografía y el mismo personaje, para que en el feed se vean como una sola pieza.

Tú escribes el guion y las escenas en un `carrusel.json`; el script `kie_carrusel.py` monta los prompts largos (con el estilo fijo de `assets/estilos/`), genera las imágenes en Kie.ai, espera lo que haga falta y las descarga.

```
~/OneDrive/Documentos/Carrusel Skill/carruseles/AAAA-MM-DD-slug/
├── carrusel.json     ← guion + escenas (lo escribes tú)
├── fuentes.md        ← enlaces y notas de investigación (lo escribes tú)
├── prompts.md        ← prompts finales enviados a Kie.ai (lo genera el script)
├── estado.json       ← taskIds y progreso (lo genera el script; permite reanudar)
├── slides/           ← slide-01.png … slide-07.png (para subir a Instagram)
├── vista-previa.png  ← todas las slides en una hoja
├── carrusel.pdf      ← una página por slide
├── caption.md        ← texto del post + hashtags (lo escribes tú)
└── publicacion.json  ← resultado de publicar en Instagram (lo genera upload_post.py)
```

`SKILL_DIR` = `~/.claude/skills/carrusel-ig`. El script solo usa la librería estándar de Python (PyMuPDF, ya instalado, para la vista previa y el PDF). La clave de Kie.ai está en `~/.env-kie`; nunca pidas que la peguen en el chat.

## 1. Entender el encargo

Por defecto: **7 slides** (portada + 5 de contenido + CTA), formato **4:5** (el vertical que mejor ocupa el feed), **2K**, estilo `rosa-palo-animado`, con avatar. Si Yadira pide otro número, respétalo (mínimo 3, máximo 10, que es el límite de Instagram).

No preguntes si la petición ya se entiende. Pregunta (una sola vez, todo junto) solo si falta algo imprescindible, por ejemplo si dice "un carrusel" sin tema.

## 2. Reunir el material según la fuente

Lee `references/guion.md` para las estructuras de cada tipo; aquí va lo esencial.

- **"Noticias de hoy" / "lo último en IA"**: busca con WebSearch noticias de IA de las últimas 24–48 h (incluye la fecha de hoy en la búsqueda). Elige las 5 más relevantes para su público (herramientas que pueda usar, lanzamientos grandes de OpenAI, Google, Anthropic, Meta…, cambios que afecten a negocios). Confirma cada noticia en una fuente fiable (web oficial o medio reconocido) y apunta los enlaces en `fuentes.md`. Si una noticia no se puede confirmar, no entra.
- **Un tema**: si incluye datos, precios, fechas o nombres de funciones, compruébalos con WebSearch; las herramientas de IA cambian cada semana y un dato viejo en una imagen no se puede corregir después de publicar.
- **Un guion o texto pegado**: respeta sus ideas y su orden; tu trabajo es condensar cada idea en una slide.
- **Un vídeo**:
  - YouTube: intenta sacar los subtítulos con `python -m yt_dlp --skip-download --write-auto-subs --write-subs --sub-langs "es.*,en.*" --sub-format vtt -o "%(id)s" URL` en la carpeta del carrusel (si `yt_dlp` no está, instálalo con `pip install yt-dlp`). Lee el `.vtt`, quédate con las ideas clave y cita el vídeo en `fuentes.md`.
  - Archivo de vídeo local o sin subtítulos: pide a Yadira la transcripción o el guion.

**No inventes** cifras, citas, funciones ni fechas. Una slide con un dato falso desacredita el canal.

## 3. Escribir el guion

Escribe para el público de un canal de IA en español: personas curiosas, emprendedoras y pymes que quieren usar la IA sin tecnicismos. Tono de Yadira: cercano, claro, con energía, de tú, español de España.

- **Portada**: titular-gancho de **máximo 8 palabras** con una promesa o una curiosidad concreta ("5 noticias de IA que cambian tu semana", "Deja de usar ChatGPT así"). Subtítulo opcional de 3–8 palabras. Opcional `antetitulo` corto (p. ej. la fecha en noticias: "7 de octubre").
- **Contenido**: **una idea por slide**, en el orden lógico de la historia, numeradas `01`, `02`… Titular de 2–6 palabras y `texto` de **15–30 palabras** (o 2–3 `puntos` de máximo 6 palabras cada uno). Cada slide tiene que entenderse sola pero empujar a la siguiente.
- **Última slide de contenido**: cierre o conclusión que prepare el CTA.
- **CTA** (fijo salvo que pida otro): `titulo` tipo "¿Te ha servido?", `texto` "Sígueme en @yadira.official para aprender IA cada día", `botones` ["Seguir", "Guardar"]. Puedes adaptar el `titulo` al tema.
- **Límite total**: el script avisa por encima de 45 palabras por slide. Menos texto = menos faltas en la imagen y más legibilidad en el móvil.
- Revisa la ortografía y las tildes de todo el texto ANTES de generar: el modelo copia lo que le das.
- Nombres propios de productos (ChatGPT, Gemini, Claude, Sora…) se escriben bien; evita símbolos raros y emojis dentro del texto de las slides.

## 4. Escribir las escenas

El campo `escena` (en **inglés**, 2–4 frases) describe solo lo que pasa en la ilustración de esa slide; el estilo, la paleta, la tipografía y el layout ya los pone el script. Hazlas concretas y variadas:

- Qué hace el avatar ("Yadira's avatar holds a giant glowing smartphone showing a chat bubble icon, looking amazed") y qué objeto 3D representa la idea.
- Varía pose y expresión entre slides (señala, sostiene, se sorprende, guiña, piensa) pero sin cambiar al personaje.
- Si una slide funciona mejor sin el avatar (p. ej. una comparativa), dilo: "no character in this slide, only 3D objects".
- Pantallas y objetos sin texto inventado: describe iconos ("a chat bubble icon", "a play button icon"). Si quieres una palabra dentro de la ilustración, añádela a `etiquetas` para que se escriba bien.
- Logos de marcas reales: solo si aportan (p. ej. noticias de un producto) y nombrándolos explícitamente en la escena; si no, usa iconos genéricos.

## 5. `carrusel.json`

```json
{
  "titulo": "5 noticias de IA de hoy",
  "estilo": "rosa-palo-animado",
  "formato": "4:5",
  "resolucion": "2K",
  "avatar": true,
  "slides": [
    {"tipo": "portada", "antetitulo": "7 de octubre", "titulo": "5 noticias de IA que no te puedes perder",
     "subtitulo": "Resumen en 1 minuto", "escena": "..."},
    {"tipo": "contenido", "numero": "01", "titulo": "...", "texto": "...", "escena": "..."},
    {"tipo": "contenido", "numero": "02", "titulo": "...", "puntos": ["...", "..."], "escena": "..."},
    {"tipo": "cta", "titulo": "¿Te ha servido?", "texto": "Sígueme en @yadira.official para aprender IA cada día",
     "botones": ["Seguir", "Guardar"], "escena": "..."}
  ]
}
```

Campos por slide: `tipo` (`portada` | `contenido` | `cta`; la primera y la última son obligatorias), `escena` (obligatoria), y los textos opcionales `antetitulo`, `titulo`, `subtitulo`, `texto`, `puntos`, `numero`, `botones`, `etiquetas`. Avanzados: `prompt_extra` (indicaciones extra en inglés), `referencias` (rutas o URLs de imágenes extra, p. ej. una captura de la herramienta) con `referencias_desc` explicando para qué sirven. En la raíz: `cuenta` (por defecto `@yadira.official`), `avatar: false` para no usarlo, `modelo`.

Hay un ejemplo completo en `references/ejemplo-carrusel.json`.

## 6. Generar

Primero comprueba los prompts sin gastar créditos:

```bash
python SKILL_DIR/scripts/kie_carrusel.py prompts "CARPETA"
```

Lee `prompts.md` por encima (que cada slide tenga sus textos y su escena). Luego genera **siempre con `run_in_background: true`** en la herramienta Bash, porque la generación tarda varios minutos y una llamada normal se cortaría:

```bash
python SKILL_DIR/scripts/kie_carrusel.py generar "CARPETA"
```

Cómo trabaja (para que sepas interpretar el log):

1. Sube el avatar maestro a Kie.ai (las subidas caducan en 24 h; el script las reutiliza hasta 20 h).
2. Genera **la portada sola** y espera. Si falla, se detiene: sin portada no hay referencia de estilo.
3. Genera el resto **en paralelo**, pasando **avatar + portada** en `image_input`, que es lo que da coherencia visual.
4. Descarga las slides (las URL de Kie.ai caducan), crea `vista-previa.png` y `carrusel.pdf`.

Espera la notificación de fin; no hagas polling ni `sleep`. Códigos de salida:

- `0`: todo listo.
- `2`: alguna slide sigue en proceso en Kie.ai tras el timeout (20 min por slide). **Relanza el mismo comando**: reanuda esas tareas con su `taskId` guardado, sin crear otras ni gastar más.
- `1`: alguna slide falló de verdad (el log dice por qué). Si es un filtro de contenido, cambia la escena; si es 402, faltan créditos (`python SKILL_DIR/scripts/kie_carrusel.py creditos`); si es 401, revisa `~/.env-kie` o en https://kie.ai/api-key habilita todos los modelos. Relanzar no repite las slides que ya están bien.

## 7. Revisar antes de entregar

Mira `vista-previa.png` con Read para comprobar la coherencia del conjunto, y después **cada slide a tamaño completo** para revisar el texto letra a letra contra el guion. Busca:

- Faltas, tildes que faltan, palabras de más o texto inventado en pantallas u objetos.
- Contador `n/total` y `@yadira.official` correctos.
- Que el avatar se parezca en todas las slides y que el estilo y la tipografía no cambien.
- Texto cortado, manos raras, el personaje tapando el texto.

Si una slide falla, corrige la causa en `carrusel.json` (texto más corto, escena más simple, una nota en `prompt_extra`) y regenérala sola:

```bash
python SKILL_DIR/scripts/kie_carrusel.py generar "CARPETA" --solo 3,5
```

Si regeneras la **portada** (`--solo 1`), el resto ya no la tendrá como referencia: regenera todo (`--solo 1,2,3,4,5,6,7`) o acepta la diferencia. **Cuidado:** si cambias el archivo de estilo, cambian los prompts de todas las slides y el script las regenera todas aunque uses `--solo` (sale 7 × 18 créditos). Para arreglar slides sueltas usa `prompt_extra` en `carrusel.json`, no el estilo. Máximo dos rondas por slide; si el texto sigue fallando, acórtalo. No entregues slides con faltas.

## 8. Entregar y publicar

1. Escribe `caption.md`: un pie de post de 3–6 líneas en el tono de Yadira (gancho, valor, pregunta para comentarios, recordatorio de seguir/guardar), 5–10 hashtags relevantes en español e inglés, y en noticias, las fuentes. Máximo 2.200 caracteres y 30 hashtags (límites de Instagram).
2. Abre la **página de resultados**: arranca `python SKILL_DIR/scripts/servidor.py` (puerto 8765) con la herramienta de previsualización o en segundo plano, y abre `http://localhost:8765/c/<carpeta>`. Ahí Yadira ve las slides, puede retocar el copy y tiene el botón **Publicar en Instagram**.
3. Envía `vista-previa.png` con SendUserFile y dile qué contiene cada slide en una línea, cuántos créditos se gastaron y el enlace de la página de resultados.

**Publicar:** el contenido de la marca de Yadira (@yadira.official) está autorizado; ella pidió (13-oct-2026) que yo programe un carrusel al día. Aun así, revisa todas las slides letra a letra antes de publicar o programar, y no publiques nunca contenido de clientes (es demo). Comandos:

```bash
python SKILL_DIR/scripts/upload_post.py publicar "CARPETA"                              # ahora
python SKILL_DIR/scripts/upload_post.py publicar "CARPETA" --programar "2026-10-15 20:00"  # hora de Madrid
```

Para programar una colección entera (cada carpeta en la fecha de su nombre, `AAAA-MM-DD-…`): `python SKILL_DIR/scripts/upload_post.py programar-coleccion "COLECCIÓN" [--hora 20:00] [--simular]`. Para cancelar una programada: `upload_post.py cancelar "CARPETA"` (devuelve el cupo). **Si cambias los scripts, reinicia el servidor de la página**: carga el módulo al arrancar y con una versión vieja puede publicar algo que ya estaba programado (pasó el 13-oct; hubo que cancelar el duplicado).

Las programadas las publica Upload-Post a esa hora aunque el ordenador esté apagado (`publicacion.json` queda en `programado` con su `job_id`). `GET /api/uploadposts/schedule` lista las programadas. Hora habitual: 20:00. Plan gratuito de Upload-Post: 10 publicaciones al mes (Basic, 24 $/mes, ilimitado).

### Cómo funciona la publicación (Upload-Post)

- Configuración en `~/.env-uploadpost`: `UPLOADPOST_API_KEY` (obligatoria) y `UPLOADPOST_USER` (el perfil de Upload-Post; si está vacío y solo hay un perfil con Instagram, se detecta solo). Nunca pidas la clave en el chat; `python SKILL_DIR/scripts/upload_post.py comprobar` valida la clave y lista los perfiles sin publicar nada.
- `scripts/upload_post.py` envía `POST https://api.upload-post.com/api/upload_photos` en multipart: `user`, `platform[]=instagram`, `title` = el copy, `photos[]` = las slides (convertidas a JPEG de 1440 px), con `async_upload=true`, y consulta `GET /api/uploadposts/status` hasta que Instagram termina.
- Una `Idempotency-Key` calculada a partir del perfil, el texto y las imágenes evita publicar dos veces si algo se reintenta. El estado queda en `publicacion.json` (`publicado` con la URL del post, `error` con el motivo o `en_proceso`, que se retoma pulsando de nuevo el botón).
- Errores típicos: 401 clave inválida; 404 perfil inexistente (revisa `UPLOADPOST_USER`); 429 límite mensual del plan; "skipped" el perfil no tiene Instagram conectado (https://app.upload-post.com/manage-users).

## Estilos, colecciones y personajes

- **Colecciones** (cada una es una carpeta y una sección de la página de resultados): `Documentos/Carrusel Skill/carruseles/` (IA, estilo `rosa-palo-animado`) y `Documentos/Carrusel Skill/Carruseles de motivación/` (mamás y amas de casa que aprenden IA, estilo `motivacion-mama`, con `calendario.md` del mes).
- **Personajes de la familia** (en `assets/marca/`, fuera de GitHub porque son fotos de sus hijos): `taylor-maestro.png` (su hija adolescente) y `familia-maestro.png` (los cinco). Se usan por slide con `"referencias": ["marca/taylor-maestro.png"]` y un `referencias_desc` que diga quién es. Fotos originales en `~/OneDrive/Imágenes/imagen contenido motivacion/`. Para crear otro personaje: `kie_carrusel.py avatar --nombre X --prompt-archivo prompt.txt --fotos …` (prompts de ejemplo en `assets/marca/prompts/`). En escenas de familia la IA tiende a dejar fuera a alguien o a inventarse un niño pequeño: usa un `prompt_extra` con los cinco descritos uno a uno (hijo ~20 años con pelo rizado; el padre con perilla; hijo ~26 con gafas redondas y gorro negro; Yadira; la hija adolescente con gafas) y "NO small children". Con disfraces es peor: mejor escenas de Yadira con Taylor.

- El estilo vive en `assets/estilos/rosa-palo-animado.json`: paleta, estilo 3D, layouts por tipo de slide, tipografía, reglas de texto, descripción del personaje y textos de referencia. Para un estilo nuevo, copia ese archivo con otro nombre, cámbialo y usa `"estilo": "nuevo-nombre"` en el carrusel.
- El avatar maestro es `assets/marca/avatar-maestro.png`. Para rehacerlo desde fotos: `python SKILL_DIR/scripts/kie_carrusel.py avatar --fotos f1.jpg f2.jpg f3.jpg --opciones 2` (crea `avatar-opcion-N.png`; copia la elegida como `avatar-maestro.png`). Las fotos originales están en `~/OneDrive/Imágenes/YADIRA/` ("Imagen para avatar", "Imagen avatar 2", "iamgen avatar 3").
