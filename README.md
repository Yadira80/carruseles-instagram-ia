# carrusel-ig

Skill de Claude Code que crea carruseles de Instagram (portada + slides de contenido + call to action) con **Nano Banana Pro** en [Kie.ai](https://kie.ai): ilustración 3D animada en rosa palo y blanco, con el avatar animado de Yadira, para el canal de IA **@yadira.official**.

## Instalación

```bash
git clone <este-repo> ~/.claude/skills/carrusel-ig
```

Crea `~/.env-kie` con tu clave (https://kie.ai/api-key):

```
KIE_API_KEY=tu_clave
```

Requisitos: Python 3.10+ (solo librería estándar) y, opcional, `pip install pymupdf` para la vista previa y el PDF.

## Uso

En Claude Code:

```
/carrusel-ig crea un carrusel con las noticias de IA de hoy
/carrusel-ig carrusel sobre cómo usar Gemini para crear contenido
/carrusel-ig convierte este guion en carrusel: …
```

El flujo completo está en [SKILL.md](SKILL.md). El script se puede usar a mano:

```bash
python scripts/kie_carrusel.py prompts CARPETA     # revisa los prompts sin gastar créditos
python scripts/kie_carrusel.py generar CARPETA     # genera (reanudable si se corta)
python scripts/kie_carrusel.py generar CARPETA --solo 3,5
python scripts/kie_carrusel.py avatar --fotos a.jpg b.jpg c.jpg
python scripts/kie_carrusel.py creditos
```

## Estructura

```
SKILL.md                         instrucciones del agente
scripts/kie_carrusel.py          generación con Kie.ai (subida de referencias, espera, reanudación)
assets/estilos/*.json            estilos visuales (paleta, layouts, tipografía, reglas de texto)
assets/marca/avatar-maestro.png  avatar de referencia
references/guion.md              guía de guion y portadas
references/ejemplo-carrusel.json ejemplo completo
```
