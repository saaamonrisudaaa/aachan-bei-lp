from pathlib import Path

path = Path("index.html")
text = path.read_text(encoding="utf-8")
if 'id="weekly-hashtag-list"' in text:
    print("Weekly hashtag section already exists")
    raise SystemExit(0)

style = '''
  <style id="weekly-hashtag-style">
    .weekly-hashtags{padding-top:18px;padding-bottom:64px}
    .weekly-hashtags-card{display:grid;grid-template-columns:minmax(220px,.75fr) minmax(0,1.25fr);gap:32px;align-items:center;padding:28px;background:linear-gradient(135deg,#fff8df,#fff);border:2px solid #38251d;border-radius:18px;box-shadow:5px 6px 0 rgba(56,37,29,.86)}
    .weekly-hashtags h2{font-size:clamp(28px,4vw,40px);line-height:1.35}
    .weekly-hashtag-copy{margin-bottom:14px;color:#775d51;font-size:14px;line-height:1.7}
    .weekly-hashtag-list{display:flex;flex-wrap:wrap;gap:10px;min-height:44px}
    .weekly-hashtag-list a{display:inline-flex;align-items:center;min-height:42px;padding:8px 14px;color:#9f3f62;background:#fff1f5;border:1px solid #f0b6ca;border-radius:999px;font-size:14px;font-weight:800;text-decoration:none;transition:transform 160ms ease,background 160ms ease,box-shadow 160ms ease}
    .weekly-hashtag-list a:hover,.weekly-hashtag-list a:focus-visible{background:#ffe3ed;box-shadow:0 5px 12px rgba(159,63,98,.14);transform:translateY(-2px)}
    .weekly-hashtag-list a:focus-visible{outline:3px solid rgba(255,127,143,.35);outline-offset:2px}
    .weekly-hashtag-note{display:block;margin-top:12px;color:#8a7064;font-size:12px}
    @media(max-width:760px){.weekly-hashtags-card{grid-template-columns:1fr;gap:18px}}
    @media(max-width:560px){.weekly-hashtags{padding:8px 16px 48px}.weekly-hashtags-card{padding:20px 16px;border-radius:14px}.weekly-hashtag-list{gap:8px}.weekly-hashtag-list a{min-height:38px;padding:7px 11px;font-size:12px}}
  </style>
'''
text = text.replace("</head>", style + "</head>", 1)

section = '''
    <section class="section weekly-hashtags" aria-labelledby="weekly-hashtag-title">
      <div class="weekly-hashtags-card">
        <div><p class="section-kicker">Weekly Hashtags</p><h2 id="weekly-hashtag-title">今週のハッシュタグ</h2></div>
        <div><p class="weekly-hashtag-copy">Instagramの投稿で紹介しているエリアや料理から、今週のおすすめタグを5つ選びました。</p><div class="weekly-hashtag-list" id="weekly-hashtag-list" aria-live="polite"></div><small class="weekly-hashtag-note">毎週月曜日に5つのタグが自動で入れ替わります。</small></div>
      </div>
    </section>
'''
text = text.replace("  </main>\n  <footer", section + "  </main>\n  <footer", 1)

script = '''
  <script id="weekly-hashtag-script">
    (() => {
      const hashtagPool = [
        '東京グルメ','東京ランチ','東京ディナー','グルメ巡り','食べ歩き','グルメ好きな人と繋がりたい',
        '東久留米グルメ','東久留米ランチ','町中華','中華料理','錦糸町グルメ','錦糸町ラーメン',
        '家系ラーメン','ラーメン好きな人と繋がりたい','神田グルメ','神田ランチ','秋葉原グルメ',
        '神田カフェ','東京カフェ','カフェ巡り','チーズケーキ','定食ランチ','東京居酒屋','あーちゃんべいグルメ'
      ];
      const list = document.getElementById('weekly-hashtag-list');
      if (!list) return;
      const today = new Date();
      const monday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - ((today.getDay() + 6) % 7));
      monday.setHours(0, 0, 0, 0);
      let state = (Math.floor(monday.getTime() / 604800000) ^ 0x9e3779b9) >>> 0;
      const shuffled = [...hashtagPool];
      for (let i = shuffled.length - 1; i > 0; i -= 1) {
        state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
        const j = state % (i + 1);
        [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
      }
      shuffled.slice(0, 5).forEach(tag => {
        const link = document.createElement('a');
        link.href = `https://www.instagram.com/explore/tags/${encodeURIComponent(tag)}/`;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = `#${tag}`;
        link.setAttribute('aria-label', `Instagramでハッシュタグ ${tag} を見る`);
        list.appendChild(link);
      });
    })();
  </script>
'''
text = text.replace("</body>", script + "</body>", 1)

if 'id="weekly-hashtag-list"' not in text or 'id="weekly-hashtag-script"' not in text:
    raise SystemExit("Failed to insert weekly hashtag section")
path.write_text(text, encoding="utf-8")
