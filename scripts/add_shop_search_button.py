from pathlib import Path

path = Path("shops.html")
text = path.read_text(encoding="utf-8")
if 'id="shop-filter-form"' in text or 'id="search-filter"' in text:
    print("Search button is already present")
    raise SystemExit(0)

style = '''
    .filter-panel{grid-template-columns:minmax(240px,1.45fr) repeat(2,minmax(160px,.7fr)) auto auto}
    .search-filter{align-self:end;min-height:46px;padding:9px 20px;color:#fff;background:#ff7f8f;border:2px solid #38251d;border-radius:999px;box-shadow:3px 4px 0 #38251d;font:inherit;font-weight:800;white-space:nowrap;cursor:pointer;transition:transform 150ms ease,box-shadow 150ms ease,background 150ms ease}
    .search-filter:hover,.search-filter:focus-visible{background:#f4667a;transform:translateY(-1px);box-shadow:4px 5px 0 #38251d}
    .search-filter:active{transform:translateY(2px);box-shadow:1px 2px 0 #38251d}
    .search-filter:focus-visible,.reset-filter:focus-visible{outline:3px solid rgba(255,127,143,.32);outline-offset:3px}
    @media(max-width:920px){.filter-panel{grid-template-columns:1fr 1fr}.filter-field:first-child{grid-column:1/-1}.search-filter,.reset-filter{width:100%}}
    @media(max-width:560px){.filter-panel{grid-template-columns:1fr}.filter-field:first-child{grid-column:auto}.search-filter,.reset-filter{min-height:48px}}
'''
text = text.replace("  </style>", style + "  </style>", 1)

old_start = '      <div class="filter-panel" aria-label="店舗の絞り込み">'
new_start = '      <form class="filter-panel" id="shop-filter-form" aria-label="店舗の絞り込み">'
if old_start not in text:
    raise SystemExit("Filter panel start was not found")
text = text.replace(old_start, new_start, 1)

old_end = '''        <button class="reset-filter" id="reset-filter" type="button">条件をクリア</button>
      </div>'''
new_end = '''        <button class="search-filter" id="search-filter" type="submit" aria-controls="directory-grid">検索する</button>
        <button class="reset-filter" id="reset-filter" type="button" aria-controls="directory-grid">条件をクリア</button>
      </form>'''
if old_end not in text:
    raise SystemExit("Filter panel end was not found")
text = text.replace(old_end, new_end, 1)

old_const = "    const grid=document.getElementById('directory-grid');"
new_const = "    const filterForm=document.getElementById('shop-filter-form');\n    const grid=document.getElementById('directory-grid');"
if old_const not in text:
    raise SystemExit("Script insertion point was not found")
text = text.replace(old_const, new_const, 1)

old_listeners = '''    search.addEventListener('input',()=>applyFilters(true));
    area.addEventListener('change',()=>applyFilters(true));
    genre.addEventListener('change',()=>applyFilters(true));
    loadMore.addEventListener('click',()=>{displayLimit+=20;applyFilters()});'''
new_listeners = '''    function updateFilterUrl(){
      const params=new URLSearchParams();
      const query=search.value.trim();
      if(query) params.set('q',query);
      if(area.value!=='all') params.set('area',area.value);
      if(genre.value!=='all') params.set('genre',genre.value);
      const suffix=params.toString();
      history.replaceState(null,'',location.pathname+(suffix?'?'+suffix:''));
    }
    filterForm.addEventListener('submit',event=>{event.preventDefault();updateFilterUrl();applyFilters(true)});
    loadMore.addEventListener('click',()=>{displayLimit+=20;applyFilters()});'''
if old_listeners not in text:
    raise SystemExit("Existing filter listeners were not found")
text = text.replace(old_listeners, new_listeners, 1)

if 'id="shop-filter-form"' not in text or 'id="search-filter"' not in text or "filterForm.addEventListener('submit'" not in text:
    raise SystemExit("Search button update did not complete")
path.write_text(text, encoding="utf-8")
