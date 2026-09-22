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
  jev  - TypeSafe API чрез typesafe-sdk; нужен е TYPESAFE_API_KEY или AI_GATEWAY_API_KEY
         (ключ vck_... от Vercel -> заявките отиват през Vercel AI Gateway)
И двата се избират от падащото меню в UI-то; вторият се зарежда при първа заявка.
Стъпка 2 иска работещ Ollama с изтеглен NAMER_MODEL. Без него новата
категория остава без име и потребителят я пише сам.
"""

import importlib.util
import json
import os
import time
import urllib.request

OTHER = "__other__"          # вариантът "друга тема" във въпроса existing
MIN_CONFIDENCE = 0.5         # под това предложението е означено като несигурно
LAYA_MODEL = "convaiinnovations/laya-multilingual"
NAMER_MODEL = os.environ.get("NAMER_MODEL", "hf.co/INSAIT-Institute/BgGPT-Gemma-3-4B-IT-GGUF:Q4_K_M")
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
GATEWAY_URL = "https://ai-gateway.vercel.sh/typesafe"  # Jev през Vercel AI Gateway


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
    @staticmethod
    def _key() -> str | None:
        return os.environ.get("TYPESAFE_API_KEY") or os.environ.get("AI_GATEWAY_API_KEY")

    @classmethod
    def _base_url(cls) -> str | None:
        """TYPESAFE_BASE_URL, ако е зададен; иначе ключ от Vercel (vck_...) -> AI Gateway."""
        key = cls._key() or ""
        return os.environ.get("TYPESAFE_BASE_URL") or (GATEWAY_URL if key.startswith("vck_") else None)

    @classmethod
    def label(cls) -> str:
        url = cls._base_url() or ""
        if url.startswith("http://127.0.0.1"):
            return "MOCK сървър (не е Jev!)"
        return "Jev (Vercel AI Gateway)" if "ai-gateway.vercel.sh" in url else "Jev (TypeSafe API)"

    @classmethod
    def unavailable(cls) -> str | None:
        """Защо бекендът не може да се ползва; None, ако може."""
        if importlib.util.find_spec("typesafe_sdk") is None:
            return "няма пакета typesafe-sdk"
        if not cls._key():
            return "няма TYPESAFE_API_KEY или AI_GATEWAY_API_KEY"
        return None

    def __init__(self):
        from typesafe_sdk import RetryPolicy, TypeSafeClient  # само ако се ползва този бекенд
        self.name = self.label()
        # при натоварване шлюзът връща 429 за десетки секунди; две повторения (по подразбиране) не стигат
        self.client = TypeSafeClient(api_key=self._key(), base_url=self._base_url(),
                                     retry=RetryPolicy(max_retries=6, backoff_max=20.0, timeout=120.0))

    def ask(self, state: dict, questions: dict) -> dict:
        from typesafe_sdk import Choice
        typed = {qid: Choice(instructions=q["instructions"], criteria=q["criteria"])
                 for qid, q in questions.items()}
        response = self.client.system_one(state=state, questions=typed)
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
        # зарежда модела в паметта сега, а не при първия пост (~30 s)
        _ollama("/api/generate", {"model": model, "keep_alive": "30m"})

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
