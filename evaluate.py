#!/usr/bin/env python3
"""Мери точността на категоризатора върху test_posts.json: свои данни, не чужд бенчмарк.

Всеки пост се оценява спрямо началните категории от seed.json - нищо не се записва.
При очаквана нова категория се мери само дали моделът е казал "друга тема";
името от локалния LLM се показва, но не се сравнява (свободен текст).
    python evaluate.py              # Laya локално
    python evaluate.py --backend jev
    python evaluate.py --posts eval_posts.json --out results-laya.json   # 50 поста, резултатите в JSON
"""

import argparse
import json
from pathlib import Path

from classifier import load_env, make_backend, make_namer, suggest

HERE = Path(__file__).resolve().parent


def load(name: str) -> dict:
    data = json.loads((HERE / name).read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", choices=["laya", "jev"], default="laya")
    parser.add_argument("--posts", default="test_posts.json", help="файл с постове и верни отговори")
    parser.add_argument("--out", help="запиши резултата за всеки пост в този JSON файл")
    args = parser.parse_args()

    categories = load("seed.json")["categories"]
    posts = load(args.posts)["posts"]
    load_env(HERE / ".env")
    backend = make_backend(args.backend)
    namer = make_namer()
    print(f"Бекенд: {backend.name}   Нови категории: {namer.name if namer else 'без име (няма Ollama или модела)'}\n")

    right = right_or_alt = uncertain = 0
    times, rows = [], []
    for post in posts:
        result = suggest(backend, namer, post["title"], post["body"], categories)
        pick, alt = result["pick"], result["alternative"]
        want = post["expected"]
        want_kind, want_label = ("new", want["new"]) if isinstance(want, dict) else ("existing", want)
        same = lambda s: bool(s) and s["kind"] == want_kind and (want_kind == "new" or s["label"] == want_label)
        hit, alt_hit = same(pick), same(alt)
        right += hit
        right_or_alt += hit or alt_hit
        uncertain += pick["uncertain"]
        times.append(result["elapsed_ms"])
        rows.append({"title": post["title"], "expected": want, "pick": pick, "alternative": alt,
                     "explain": result["explain"], "hit": hit, "alt_hit": bool(alt_hit), "elapsed_ms": result["elapsed_ms"]})
        mark = "вярно " if hit else ("алтерн" if alt_hit else "ГРЕШНО")
        label = ("нова: " + (pick["label"] or "?")) if pick["kind"] == "new" else pick["label"]
        flag = " ?" if pick["uncertain"] else ""
        print(f"{mark}  {label:24} conf={pick['confidence']:.2f}{flag:2}  "
              f"(очаквано: {want_label})  | {post['title']}")

    n = len(posts)
    times.sort()
    print(f"\nВярно предложение: {right}/{n}   Вярно с алтернативата (едно щракване): {right_or_alt}/{n}   "
          f"Означени като несигурни: {uncertain}/{n}")
    print(f"Време на заявка: медиана {times[n // 2]} ms, най-бавно {times[-1]} ms")
    if args.out:
        summary = {"backend": backend.name, "namer": namer.name if namer else None, "posts": args.posts,
                   "right": right, "right_or_alt": right_or_alt, "uncertain": uncertain, "n": n}
        Path(args.out).write_text(json.dumps({**summary, "results": rows}, ensure_ascii=False, indent=1),
                                  encoding="utf-8")


if __name__ == "__main__":
    main()
