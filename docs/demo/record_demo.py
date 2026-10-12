"""Record the demo video: Jev on an existing and a new category, then Laya.

Start the app first (python app.py --port 8779), then:
    python docs/demo/record_demo.py OUT_DIR
and encode the raw .webm (commands in docs/demo/README.md)."""
import json, time, sys, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
URL = "http://localhost:8779/"
posts = {p["title"]: p for p in json.loads((Path(__file__).resolve().parents[2] / "test_posts.json").read_text())["posts"]}
W, H = 1056, 704

CURSOR = """
window.addEventListener('DOMContentLoaded', () => {
  const c = document.createElement('div'); c.id='__cur';
  c.style.cssText='position:fixed;left:0;top:0;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;'+
   'background:rgba(255,255,255,.35);border:2px solid rgba(255,255,255,.9);box-shadow:0 0 0 1px rgba(0,0,0,.5);'+
   'pointer-events:none;z-index:99999;transition:transform .12s';
  document.body.appendChild(c);
  addEventListener('mousemove', e => { c.style.left=e.clientX+'px'; c.style.top=e.clientY+'px'; }, true);
  addEventListener('mousedown', () => c.style.transform='scale(.7)', true);
  addEventListener('mouseup', () => c.style.transform='', true);
  const cap = document.createElement('div'); cap.id='__cap';
  cap.style.cssText='position:fixed;left:50%;bottom:22px;transform:translateX(-50%);max-width:86%;padding:10px 18px;'+
   'border-radius:10px;background:rgba(15,18,24,.92);color:#fff;font:600 17px/1.35 system-ui,sans-serif;'+
   'box-shadow:0 6px 24px rgba(0,0,0,.45);border:1px solid rgba(255,255,255,.15);z-index:99998;pointer-events:none;opacity:0;transition:opacity .35s;text-align:center';
  document.body.appendChild(cap);
});
"""

t0 = None
cuts = []
def now(): return time.time() - t0

def api(path, d):
    r = urllib.request.Request("http://localhost:8779" + path, json.dumps(d).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=120))

with sync_playwright() as p:
    b = p.chromium.launch()
    # warm: set backend in localStorage before recording
    api("/api/reset", {})
    ctx = b.new_context(viewport={"width": W, "height": H}, color_scheme="dark",
                        record_video_dir=str(OUT), record_video_size={"width": W, "height": H}, locale="bg-BG")
    ctx.add_init_script("try{localStorage.setItem('blog.backend','jev')}catch(e){}")
    ctx.add_init_script(CURSOR)
    page = ctx.new_page()
    t0 = time.time()
    page.goto(URL); page.wait_for_selector("#examples .chip")
    page.evaluate("""() => { const s=document.querySelector('#backend'); if (s.value!=='jev'){s.value='jev'; s.dispatchEvent(new Event('change'));} }""")
    page.mouse.move(W/2, H/2)

    def cap(text):
        page.evaluate("t => { const c=document.getElementById('__cap'); if(!t){c.style.opacity=0;return;} c.textContent=t; c.style.opacity=1; }", text)

    def goto(sel, click=True, steps=28):
        box = page.locator(sel).first.bounding_box()
        page.mouse.move(box["x"] + box["width"]/2, box["y"] + box["height"]/2, steps=steps)
        page.wait_for_timeout(150)
        if click: page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)

    def write(title):
        goto("#title"); page.keyboard.type(title, delay=45)
        goto("#body"); page.keyboard.type(posts[title]["body"][:95], delay=12)
        page.evaluate("b => { const t=document.querySelector('#body'); t.value=b; }", posts[title]["body"])

    def suggest():
        goto("#suggest")
        s = now()
        page.wait_for_selector("#suggestion .sugg-head", timeout=120000)
        e = now()
        if e - s > 1.2: cuts.append([s + 0.6, e - 0.3])
        page.wait_for_timeout(300)
        page.evaluate("() => document.querySelector('#suggestion').scrollIntoView({behavior:'smooth', block:'center'})")
        page.wait_for_timeout(500)

    page.wait_for_timeout(1200)
    cap("A blog where a System One model picks the category of every new post")
    page.wait_for_timeout(2600)
    # Scene 1: Jev, existing category
    cap("Jev: an existing category, with its confidence")
    write("Ипотека или наем?")
    suggest()
    page.wait_for_timeout(2600)
    goto("#suggestion .btn-primary")
    page.wait_for_timeout(1400)
    # Scene 2: Jev, new topic, BgGPT names it
    cap("A topic the blog doesn't have yet: a local LLM (BgGPT) names the new category")
    page.evaluate("() => window.scrollTo({top:0,behavior:'smooth'})"); page.wait_for_timeout(600)
    write("Домати на балкона")
    suggest()
    page.wait_for_timeout(2400)
    goto("#suggestion summary")
    page.wait_for_timeout(500)
    page.evaluate("() => document.querySelector('#suggestion details').scrollIntoView({behavior:'smooth', block:'center'})")
    page.wait_for_timeout(700)
    cap("\"How the model decided\": the probability of every category")
    page.wait_for_timeout(3200)
    goto("#suggestion .btn-primary")
    page.wait_for_timeout(900)
    page.evaluate("() => window.scrollTo({top:0,behavior:'smooth'})"); page.wait_for_timeout(700)
    cap("Published: the new category is in the blog from now on")
    page.wait_for_timeout(2600)
    # Scene 3: Laya on a new topic
    cap("Same task with Laya, the open model running locally on the CPU...")
    goto("#backend", click=False)
    page.wait_for_timeout(300)
    page.select_option("#backend", "laya")
    page.wait_for_timeout(1200)
    write("Как избягах първия си маратон")
    suggest()
    cap("...Laya files a marathon post under \"Travel\". On 50 posts: Jev 48/50, Laya 36/50")
    page.wait_for_timeout(4200)
    cap("")
    page.wait_for_timeout(600)
    vid = page.video.path()
    ctx.close(); b.close()

(OUT / "cuts.json").write_text(json.dumps({"video": str(vid), "cuts": cuts, "end": now()}))
print(vid, cuts)
