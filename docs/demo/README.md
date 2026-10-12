# Demo video

`jev-vs-laya-demo.mp4` / `.webm` (48 s, 1056x704, no sound) and the poster
`jev-vs-laya-poster.jpg`, for web pages. Recorded with Playwright by
`record_demo.py` against the running app, with captions in English:

1. Jev puts a mortgage post in an existing category, with its confidence.
2. A post about tomatoes on the balcony: Jev says "new topic", BgGPT names it
   "Градинарство", the "Как реши моделът" panel shows the probabilities.
3. The same task with Laya: a marathon post goes to "Пътувания", marked unsure.

Re-record (the recording resets `data/blog.json`; back it up first):

```bash
python app.py --port 8779 &
python docs/demo/record_demo.py /tmp/rec
ffmpeg -i /tmp/rec/page@*.webm -vf "fps=24,format=yuv420p" -c:v libx264 -preset slow \
  -crf 26 -tune animation -movflags +faststart -an docs/demo/jev-vs-laya-demo.mp4
ffmpeg -i /tmp/rec/page@*.webm -vf fps=24 -c:v libvpx-vp9 -b:v 0 -crf 40 -row-mt 1 -an \
  docs/demo/jev-vs-laya-demo.webm
ffmpeg -ss 25.5 -i /tmp/rec/page@*.webm -frames:v 1 -q:v 3 docs/demo/jev-vs-laya-poster.jpg
```
