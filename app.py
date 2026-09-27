#!/usr/bin/env python3
"""Блог с автоматични категории - уеб приложението.

    python app.py                  # Laya локално
    python app.py --backend jev    # започва с Jev; иска DIGITALOCEAN_MODEL_ACCESS_KEY, OPENROUTER_API_KEY или AI_GATEWAY_API_KEY
    python app.py --port 8771
После отвори http://127.0.0.1:8770

Моделът за стъпка 1 (Laya или Jev) се сменя от падащото меню в UI-то;
--backend избира кой е по подразбиране и се зарежда при старта. Ключовете
могат да са и в .env до app.py.

Само стандартната библиотека + бекенда (laya или typesafe-sdk). Имената на нови
категории идват от локален LLM през Ollama (HTTP). Състоянието на
блога е в data/blog.json; бутонът "Нулирай" го връща към seed.json.
"""

import argparse
import json
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from classifier import BACKENDS, backend_options, load_env, make_backend, make_namer, suggest

HERE = Path(__file__).resolve().parent
SEED = HERE / "seed.json"
DATA = HERE / "data" / "blog.json"
MAX_TEXT = 20_000  # символа; System One моделите четат текст, не романи

lock = threading.Lock()  # един модел, една заявка наведнъж; пази и файла
backends = {}  # id -> зареден бекенд; вторият се зарежда при първа заявка към него
default_backend = "laya"
namer = None  # локален LLM за имена на нови категории; None без Ollama


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


def load_blog() -> dict:
    if not DATA.exists():
        DATA.parent.mkdir(exist_ok=True)
        shutil.copy(SEED, DATA)
    return read_json(DATA)


def save_blog(blog: dict) -> None:
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(blog, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA)


def get_backend(kind: str):
    """Зареден бекенд по id; зарежда го при нужда. Викай под lock."""
    if kind not in backends:
        options = {o["id"]: o for o in backend_options()}
        if kind not in options:
            raise ValueError(f"Няма такъв модел: {kind}")
        if options[kind]["unavailable"]:
            raise ValueError(f"{options[kind]['name']}: {options[kind]['unavailable']}")
        backends[kind] = make_backend(kind)
    return backends[kind]


def public_state(blog: dict) -> dict:
    counts = {name: 0 for name in blog["categories"]}
    for post in blog["posts"]:
        counts[post["category"]] = counts.get(post["category"], 0) + 1
    return {
        # редът на категориите е редът на създаване - от него зависи цветът
        "categories": [{"name": n, "description": d, "count": counts[n]} for n, d in blog["categories"].items()],
        "posts": list(reversed(blog["posts"])),  # най-новите отгоре
        "backend": default_backend,
        "backends": [{**o, "loaded": o["id"] in backends} for o in backend_options()],
        "namer": namer.name if namer else None,
        "examples": [{"title": p["title"], "body": p["body"]} for p in read_json(HERE / "test_posts.json")["posts"]],
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload, content_type="application/json; charset=utf-8") -> None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            with lock:
                self._send(200, public_state(load_blog()))
        else:
            self._send(404, {"error": "Няма такава страница"})

    def do_POST(self) -> None:
        try:
            body = self._body()
        except (json.JSONDecodeError, ValueError):
            self._send(400, {"error": "Невалиден JSON"})
            return
        title = str(body.get("title", "")).strip()[:300]
        text = str(body.get("body", "")).strip()[:MAX_TEXT]

        if self.path == "/api/suggest":
            if not (title or text):
                self._send(400, {"error": "Напиши заглавие или текст"})
                return
            with lock:
                blog = load_blog()
                try:
                    backend = get_backend(str(body.get("backend") or default_backend))
                    result = suggest(backend, namer, title, text, blog["categories"])
                except Exception as error:  # мрежа, ключ, лимит - показваме го в UI-то
                    self._send(502, {"error": f"Моделът не отговори: {error}"})
                    return
            self._send(200, result)

        elif self.path == "/api/posts":
            category = str(body.get("category", "")).strip()[:60]
            if not (title and category):
                self._send(400, {"error": "Трябват заглавие и категория"})
                return
            with lock:
                blog = load_blog()
                if category not in blog["categories"]:
                    # описанието идва от локалния LLM или от потребителя;
                    # моделът го използва при следващите постове
                    description = str(body.get("description") or category).strip()[:200]
                    blog["categories"][category] = description
                blog["posts"].append({
                    "title": title, "body": text, "category": category,
                    "suggested": str(body.get("suggested", ""))[:60] or None,  # за мерене: приет или поправен
                })
                save_blog(blog)
                self._send(200, public_state(blog))

        elif self.path == "/api/reset":
            with lock:
                shutil.copy(SEED, DATA)
                self._send(200, public_state(load_blog()))
        else:
            self._send(404, {"error": "Няма такъв адрес"})

    def log_message(self, fmt, *args) -> None:
        sys.stderr.write("blog: " + fmt % args + "\n")


def main() -> None:
    global default_backend, namer
    load_env(HERE / ".env")
    parser = argparse.ArgumentParser(description="Блог с автоматични категории")
    parser.add_argument("--backend", choices=["laya", "jev"], default="laya")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()

    default_backend = args.backend
    print(f"Зареждам {BACKENDS[args.backend].label()}...", flush=True)
    started = time.monotonic()
    backend = get_backend(args.backend)
    print(f"{backend.name}: зареден ({time.monotonic() - started:.0f} s)", flush=True)
    print("Проверявам Ollama...", flush=True)
    namer = make_namer()
    if namer:
        print(f"Зареждам {namer.name} във фона...", flush=True)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        sys.exit(f"Порт {args.port} е зает. Пусни с --port {args.port + 1}")
    others = [f"{o['name']} ({o['unavailable'] or 'при първа заявка'})" for o in backend_options() if o["id"] != args.backend]
    print(f"Бекенд: {backend.name}; в менюто още: {', '.join(others)}\nНови категории: {namer.name if namer else 'ръчно (няма Ollama или модела)'}\nОтвори http://127.0.0.1:{args.port}  (Ctrl+C за спиране)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
