"""Категоризатор на блог постове - логиката, без уеб частта. Всичко е локално и безплатно.

Две стъпки:
  1. System One модел, един въпрос Choice: към коя от съществуващите категории
     е постът, или "друга тема". Бързо, пуска се за всеки пост.
  2. Само при "друга тема": малък генеративен LLM (BgGPT 3.0 4B през Ollama)
     измисля име и кратко английско описание на новата категория.
     System One моделът не пише текст - само избира, затова тази стъпка е отделна.
Показваме и втората възможност, за да се поправи с едно щракване.
Увереността (confidence) под MIN_CONFIDENCE значи "провери ме".

Бекендове за стъпка 1 (еднакъв въпрос, еднакъв формат на отговора):
  laya - convaiinnovations/laya-multilingual локално на процесора (по подразбиране)
  jev  - TypeSafe API чрез typesafe-sdk; нужен е поне един от DIGITALOCEAN_MODEL_ACCESS_KEY (DigitalOcean),
         OPENROUTER_API_KEY (OpenRouter), AI_GATEWAY_API_KEY (Vercel AI Gateway) или TYPESAFE_API_KEY.
         При няколко ключа се опитват по реда им в .env; ако един откаже, минава към следващия.
И двата се избират от падащото меню в UI-то; вторият се зарежда при първа заявка.
Стъпка 2 иска работещ Ollama с изтеглен NAMER_MODEL. Без него новата
категория остава без име и потребителят я пише сам.
"""

import importlib.util
import json
import os
import threading
import time
import urllib.request

OTHER = "__other__"          # вариантът "друга тема" във въпроса existing
MIN_CONFIDENCE = 0.5         # под това предложението е означено като несигурно
LAYA_MODEL = "convaiinnovations/laya-multilingual"
NAMER_MODEL = os.environ.get("NAMER_MODEL", "hf.co/INSAIT-Institute/BgGPT-Gemma-3-4B-IT-GGUF:Q4_K_M")
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
DO_URL = "https://inference.do-ai.run"                 # Jev през DigitalOcean Serverless Inference
DO_MODEL = "typesafe-jev-latest"                        # името на Jev в DigitalOcean
OPENROUTER_URL = "https://openrouter.ai/api"            # Jev през OpenRouter
GATEWAY_URL = "https://ai-gateway.vercel.sh/typesafe"  # Jev през Vercel AI Gateway


ENV_ORDER: list[str] = []  # ключовете от .env по реда им; по него се избира маршрутът за Jev


def load_env(path) -> None:
    """Прост .env: редове KEY=value. Не презаписва вече зададени променливи."""
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return
    for line in lines:
        key, sep, value = line.removeprefix("export ").partition("=")
        value = value.strip().strip("'\"")
        if sep and not key.lstrip().startswith("#") and value and "${" not in value:
            os.environ.setdefault(key.strip(), value)
            ENV_ORDER.append(key.strip())


def build_questions(categories: dict[str, str]) -> dict:
    """Въпросът във формата на API-то. categories: етикет -> описание."""
    return {
        "existing": {
            "type": "choice",
            "instructions": "Which blog category does this post belong to?",
            "criteria": {**categories, OTHER: "other topic"},
        },
    }


class JevBackend:
    """Jev през всеки маршрут, за който има ключ. Ред: както са ключовете в .env;
    ако първият маршрут откаже, заявката минава през следващия."""
    ROUTES = {  # променлива с ключа -> (адрес, име в UI-то, модел)
        "DIGITALOCEAN_MODEL_ACCESS_KEY": (DO_URL, "DigitalOcean", DO_MODEL),
        "OPENROUTER_API_KEY": (OPENROUTER_URL, "OpenRouter", None),
        "AI_GATEWAY_API_KEY": (GATEWAY_URL, "Vercel AI Gateway", None),
        "TYPESAFE_API_KEY": (None, "TypeSafe API", None),  # адрес от TYPESAFE_BASE_URL или SDK-то
    }

    @classmethod
    def _routes(cls) -> list[str]:
        """Променливите със зададен ключ: първо по реда в .env, после останалите по реда в ROUTES."""
        present = [var for var in cls.ROUTES if os.environ.get(var)]
        return sorted(present, key=lambda var: ENV_ORDER.index(var) if var in ENV_ORDER else len(ENV_ORDER))

    @classmethod
    def label(cls) -> str:
        names = [cls.ROUTES[var][1] for var in cls._routes()]
        return f"Jev ({' -> '.join(names)})" if names else "Jev"

    @classmethod
    def unavailable(cls) -> str | None:
        """Защо бекендът не може да се ползва; None, ако може."""
        if importlib.util.find_spec("typesafe_sdk") is None:
            return "няма пакета typesafe-sdk"
        if not cls._routes():
            return "няма " + ", ".join(cls.ROUTES)
        return None

    def __init__(self):
        from typesafe_sdk import RetryPolicy, TypeSafeClient  # само ако се ползва този бекенд
        self.name = self.label()
        self.clients = []  # (име, клиент) по реда на опитване
        routes = self._routes()
        for i, var in enumerate(routes):
            url, route_name, model = self.ROUTES[var]
            # при натоварване шлюзът връща 429 за десетки секунди; две повторения (по подразбиране)
            # не стигат, освен ако има следващ маршрут - тогава е по-бързо да минем към него
            retries = 6 if i == len(routes) - 1 else 2
            client = TypeSafeClient(api_key=os.environ[var], base_url=url,
                                    model=os.environ.get("TYPESAFE_DEFAULT_MODEL") or model,
                                    retry=RetryPolicy(max_retries=retries, backoff_max=20.0, timeout=120.0))
            self.clients.append((route_name, client))

    def ask(self, state: dict, questions: dict) -> dict:
        from typesafe_sdk import Choice, TypeSafeError
        typed = {qid: Choice(instructions=q["instructions"], criteria=q["criteria"])
                 for qid, q in questions.items()}
        for i, (route_name, client) in enumerate(self.clients):
            try:
                response = client.system_one(state=state, questions=typed)
                break
            except TypeSafeError as error:
                if i == len(self.clients) - 1:
                    raise
                print(f"Jev през {route_name} не отговори ({error}); опитвам {self.clients[i + 1][0]}", flush=True)
        return {qid: {"choice": a.choice, "confidence": a.confidence, "probabilities": dict(a.probabilities)}
                for qid, a in response.choices.items()}


class LayaBackend:
    name = "Laya multilingual (локално, CPU)"

    @classmethod
    def label(cls) -> str:
        return cls.name

    @staticmethod
    def unavailable() -> str | None:
        return "няма пакета laya" if importlib.util.find_spec("laya") is None else None

    def __init__(self, model: str = LAYA_MODEL):
        os.environ.setdefault("USE_TF", "0")
        import laya
        self.agent = laya.load(model)  # 15-45 s; зарежда се веднъж при старта

    def ask(self, state: dict, questions: dict) -> dict:
        answers = self.agent.predict(state, questions)["answers"]
        return {qid: {"choice": a["choice"], "confidence": a["confidence"], "probabilities": a["probabilities"]}
                for qid, a in answers.items()}


BACKENDS = {"laya": LayaBackend, "jev": JevBackend}


def make_backend(kind: str = "laya"):
    return BACKENDS[kind]()


def backend_options() -> list[dict]:
    """За падащото меню: id, име и защо не е достъпен (None - достъпен е)."""
    return [{"id": kind, "name": cls.label(), "unavailable": cls.unavailable()} for kind, cls in BACKENDS.items()]


def _ollama(path: str, payload: dict | None = None, timeout: float = 120) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(OLLAMA_URL + path, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


class OllamaNamer:
    """Измисля име на нова категория с локален LLM. Извиква се само при "друга тема"."""

    SCHEMA = {
        "type": "object",
        "properties": {"label": {"type": "string"}, "description": {"type": "string"}},
        "required": ["label", "description"],
    }

    def __init__(self, model: str = NAMER_MODEL):
        self.model = model
        self.name = model.split("/")[-1].split(":")[0] + " (Ollama)"
        # зарежда модела в паметта във фона (~30 s), за да не чака стартът
        threading.Thread(target=self._warm_up, daemon=True).start()

    def _warm_up(self) -> None:
        started = time.monotonic()
        try:
            _ollama("/api/generate", {"model": self.model, "keep_alive": "30m"})
        except OSError as error:
            print(f"{self.name}: не се зареди ({error})", flush=True)
            return
        print(f"{self.name}: зареден ({time.monotonic() - started:.0f} s)", flush=True)

    @staticmethod
    def _prompt(title: str, body: str, categories: dict[str, str]) -> str:
        existing = "\n".join(f"- {k} ({v})" for k, v in categories.items()) or "(няма)"
        return ("Блог постът по-долу не пасва на нито една от съществуващите категории. "
                "Измисли нова категория за него.\n"
                "label: име на български, 1-3 думи, достатъчно общо и за бъдещи постове по темата, "
                "в стила на съществуващите.\n"
                "description: същото на английски, 1-3 думи с малки букви.\n\n"
                f"Съществуващи категории:\n{existing}\n\nЗаглавие: {title}\n\n{body}")

    def name_new(self, title: str, body: str, categories: dict[str, str]) -> dict:
        # един пример (few-shot): без него описанието често излиза на български
        example = [
            {"role": "user", "content": self._prompt(
                "Първият ми концерт на цигулка", "Три години уроци, треска преди сцената и какво научих за Бах.",
                categories)},
            {"role": "assistant", "content": '{"label": "Музика", "description": "music"}'},
        ]
        response = _ollama("/api/chat", {
            "model": self.model, "stream": False, "keep_alive": "30m",
            "format": self.SCHEMA,  # отговорът е JSON по схемата
            "options": {"temperature": 0},
            "messages": example + [{"role": "user", "content": self._prompt(title, body, categories)}],
        })
        data = json.loads(response["message"]["content"])
        return {"label": data["label"].strip()[:60], "description": data["description"].strip().lower()[:200]}


def make_namer():
    """OllamaNamer, ако Ollama върви и моделът е изтеглен; иначе None - името се пише ръчно."""
    try:
        models = {m["name"] for m in _ollama("/api/tags", timeout=3)["models"]}
    except OSError:
        return None
    return OllamaNamer() if NAMER_MODEL in models else None


def _ranked(probabilities: dict[str, float], keep=None) -> list[dict]:
    items = [(k, p) for k, p in probabilities.items() if keep is None or keep(k)]
    items.sort(key=lambda kp: -kp[1])
    return [{"label": k, "p": round(p, 3)} for k, p in items]


def suggest(backend, namer, title: str, body: str, categories: dict[str, str]) -> dict:
    """Предложение за категория + всичко, което UI-то показва като обяснение."""
    started = time.perf_counter()
    if categories:
        existing = backend.ask({"title": title, "body": body}, build_questions(categories))["existing"]
    else:  # празен блог: няма от какво да се избира, направо нова категория
        existing = {"choice": OTHER, "confidence": 1.0, "probabilities": {OTHER: 1.0}}
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    ranked = _ranked(existing["probabilities"], keep=lambda k: k != OTHER)

    if existing["choice"] != OTHER:
        pick = {"kind": "existing", **ranked[0]}
        alternative = {"kind": "existing", **ranked[1]} if len(ranked) > 1 else None
    else:
        started = time.perf_counter()
        named = namer.name_new(title, body, categories) if namer else {"label": "", "description": ""}
        pick = {"kind": "new", **named, "p": round(existing["probabilities"][OTHER], 3)}
        alternative = {"kind": "existing", **ranked[0]} if ranked else None
        elapsed_ms += round((time.perf_counter() - started) * 1000)

    pick["confidence"] = round(existing["confidence"], 3)
    pick["uncertain"] = pick["confidence"] < MIN_CONFIDENCE
    return {
        "pick": pick,
        "alternative": alternative,
        "explain": {
            "existing": _ranked(existing["probabilities"]),
            "existing_confidence": round(existing["confidence"], 3),
        },
        "backend": backend.name,
        "namer": namer.name if namer else None,
        "elapsed_ms": elapsed_ms,
    }
