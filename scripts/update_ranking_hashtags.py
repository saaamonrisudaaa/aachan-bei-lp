from pathlib import Path

path = Path("index.html")
text = path.read_text(encoding="utf-8")
replacements = {
    '<div class="tags"><span>東京</span><span>東久留米</span><span>中華料理</span><span>いいね 101</span>': '<div class="tags"><span>#東京</span><span>#東久留米</span><span>#中華料理</span><span>いいね 101</span>',
    '<div class="tags"><span>東京</span><span>錦糸町</span><span>ラーメン</span>': '<div class="tags"><span>#東京</span><span>#錦糸町</span><span>#ラーメン</span>',
    '<div class="tags"><span>東京</span><span>カフェ</span><span>いいね 1,630</span>': '<div class="tags"><span>#東京</span><span>#神田</span><span>#カフェ</span><span>いいね 1,630</span>'
}
for old, new in replacements.items():
    if old not in text:
        raise SystemExit(f"Ranking tag pattern was not found: {old}")
    text = text.replace(old, new, 1)
for expected in ('#東久留米', '#錦糸町', '#神田', '#中華料理', '#ラーメン', '#カフェ'):
    if expected not in text:
        raise SystemExit(f"Missing hashtag: {expected}")
path.write_text(text, encoding="utf-8")
