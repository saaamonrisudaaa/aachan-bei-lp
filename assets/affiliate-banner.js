(() => {
  "use strict";

  if (window.__achanbayAffiliateBannerLoaded) return;
  window.__achanbayAffiliateBannerLoaded = true;

  const loader = document.currentScript;
  const configUrl = loader?.dataset.config || "./assets/affiliate-banners.json";
  const prefix = "achanbay_affiliate_banner_";

  const storage = {
    get(key) {
      try { return window.localStorage.getItem(prefix + key); } catch (_) { return null; }
    },
    set(key, value) {
      try { window.localStorage.setItem(prefix + key, String(value)); } catch (_) {}
    }
  };

  const normalise = value => String(value || "").trim().toLowerCase();
  const asList = value => Array.isArray(value) ? value.map(normalise) : [];
  const now = Date.now();

  function wildcardMatch(pattern, value) {
    if (pattern === "*" || pattern === "all") return true;
    if (!pattern.includes("*")) return pattern === value;
    const escaped = pattern.split("*").map(part => part.replace(/[|\\{}()[\]^$+?.-]/g, "\\$&")).join(".*");
    return new RegExp("^" + escaped + "$").test(value);
  }

  function listMatches(list, value) {
    const values = asList(list);
    return !values.length || values.some(item => wildcardMatch(item, normalise(value)));
  }

  function pageCategory(path) {
    if (/yayaya-kinshicho/.test(path)) return "ramen";
    if (/pot-higashikurume|sinensis-kichijoji/.test(path)) return "chinese";
    if (/sta-kanda/.test(path)) return "cafe";
    if (/genre-ramen\.html/.test(path)) return "ramen";
    if (/genre-chinese\.html/.test(path)) return "chinese";
    if (/genre-cafe\.html/.test(path)) return "cafe";
    if (/genre-japanese\.html/.test(path)) return "japanese";
    if (/area-|shops\.html/.test(path)) return "directory";
    if (path === "/" || /index\.html/.test(path)) return "home";
    return "article";
  }

  function trafficSource() {
    const params = new URLSearchParams(window.location.search);
    const campaignSource = normalise(params.get("utm_source"));
    if (campaignSource) return campaignSource;
    if (!document.referrer) return "direct";
    try {
      const host = new URL(document.referrer).hostname;
      if (/instagram|facebook|t\.co|twitter|x\.com/.test(host)) return "social";
      if (/google|bing|yahoo/.test(host)) return "search";
      if (host === window.location.hostname) return "internal";
      return "referral";
    } catch (_) {
      return "other";
    }
  }

  function visitorType() {
    const seen = storage.get("seen");
    storage.set("seen", now);
    return seen ? "returning" : "new";
  }

  function context(config) {
    const settings = config.personalization || {};
    return {
      path: normalise(window.location.pathname || "/"),
      category: settings.usePageCategory === false ? "all" : pageCategory(window.location.pathname),
      device: settings.useDevice === false ? "all" : (window.matchMedia("(max-width: 640px)").matches ? "mobile" : "desktop"),
      source: settings.useTrafficSource === false ? "all" : trafficSource(),
      visitorType: settings.useVisitorType === false ? "all" : visitorType()
    };
  }

  function isWithinSchedule(ad) {
    const start = ad.startAt ? Date.parse(ad.startAt) : null;
    const end = ad.endAt ? Date.parse(ad.endAt) : null;
    return (!start || start <= now) && (!end || end >= now);
  }

  function isFrequencyCapped(ad) {
    const hours = Number(ad.frequencyCapHours || 0);
    if (!hours) return false;
    const lastShown = Number(storage.get("impression_" + ad.id) || 0);
    return lastShown && now - lastShown < hours * 60 * 60 * 1000;
  }

  function isEligible(ad, ctx) {
    if (!ad || ad.active === false || !ad.id || !ad.destinationUrl) return false;
    if (!isWithinSchedule(ad) || isFrequencyCapped(ad)) return false;
    const target = ad.targeting || {};
    return listMatches(target.paths, ctx.path) &&
      listMatches(target.categories, ctx.category) &&
      listMatches(target.devices, ctx.device) &&
      listMatches(target.sources, ctx.source) &&
      listMatches(target.visitorTypes, ctx.visitorType);
  }

  function relevanceScore(ad, ctx) {
    const target = ad.targeting || {};
    let score = Number(ad.priority || 0);
    if (asList(target.paths).length && !asList(target.paths).some(value => value === "*" || value === "all")) score += 40;
    if (asList(target.categories).length && !asList(target.categories).includes("all")) score += 35;
    if (asList(target.devices).length && !asList(target.devices).includes("all")) score += 15;
    if (asList(target.sources).length && !asList(target.sources).includes("all")) score += 25;
    if (asList(target.visitorTypes).length && !asList(target.visitorTypes).includes("all")) score += 10;
    return score;
  }

  function chooseAd(ads, ttlHours, ctx) {
    const bestScore = Math.max(...ads.map(ad => relevanceScore(ad, ctx)));
    const candidates = ads.filter(ad => relevanceScore(ad, ctx) === bestScore);
    const storedId = storage.get("selected_id");
    const selectedAt = Number(storage.get("selected_at") || 0);
    const ttl = Math.max(0, Number(ttlHours || 0)) * 60 * 60 * 1000;
    const stored = candidates.find(ad => ad.id === storedId);
    if (stored && (!ttl || now - selectedAt < ttl)) return stored;

    const total = candidates.reduce((sum, ad) => sum + Math.max(0, Number(ad.weight || 1)), 0);
    let cursor = Math.random() * (total || candidates.length);
    const selected = candidates.find(ad => {
      cursor -= Math.max(0, Number(ad.weight || 1));
      return cursor <= 0;
    }) || candidates[0];

    storage.set("selected_id", selected.id);
    storage.set("selected_at", now);
    return selected;
  }

  function track(eventName, ad, ctx) {
    const parameters = {
      ad_id: ad.id,
      asp: ad.asp || "unknown",
      page_category: ctx.category,
      device_type: ctx.device,
      traffic_source: ctx.source,
      visitor_type: ctx.visitorType
    };
    if (typeof window.gtag === "function") window.gtag("event", eventName, parameters);
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push({ event: eventName, ...parameters });
  }

  function addStyles() {
    const style = document.createElement("style");
    style.id = "achanbay-affiliate-banner-style";
    style.textContent = `
      .achanbay-affiliate-banner{position:fixed;z-index:9998;right:16px;bottom:16px;left:16px;display:flex;max-width:760px;min-height:92px;margin:auto;align-items:center;gap:14px;padding:12px 52px 12px 12px;color:#38251d;background:rgba(255,250,243,.98);border:2px solid #38251d;border-radius:16px;box-shadow:6px 8px 0 rgba(56,37,29,.9),0 18px 42px rgba(56,37,29,.24);font-family:"Noto Sans JP",sans-serif;transform:translateY(calc(100% + 40px));opacity:0;transition:transform .3s ease,opacity .3s ease}
      .achanbay-affiliate-banner.is-visible{transform:translateY(0);opacity:1}
      .achanbay-affiliate-banner__image{display:block;flex:0 0 68px;width:68px;height:68px;object-fit:cover;background:#fff;border:1px solid #ead8c7;border-radius:12px}
      .achanbay-affiliate-banner__body{min-width:0;flex:1}
      .achanbay-affiliate-banner__label{display:inline-flex;margin-bottom:3px;padding:2px 7px;color:#8e294c;background:#ffe5ee;border-radius:999px;font-size:10px;font-weight:800}
      .achanbay-affiliate-banner__title{display:block;margin:0;color:#38251d;font-size:16px;font-weight:800;line-height:1.4}
      .achanbay-affiliate-banner__description{margin:3px 0 0;color:#775d51;font-size:12px;line-height:1.5}
      .achanbay-affiliate-banner__cta{display:inline-flex;flex:0 0 auto;align-items:center;justify-content:center;min-height:42px;padding:8px 15px;color:#fff;background:#ff6f83;border:2px solid #38251d;border-radius:999px;box-shadow:2px 3px 0 #38251d;font-size:13px;font-weight:800;text-decoration:none;white-space:nowrap}
      .achanbay-affiliate-banner__cta:hover,.achanbay-affiliate-banner__cta:focus-visible{background:#f35670;transform:translateY(-1px)}
      .achanbay-affiliate-banner__close{position:absolute;top:7px;right:8px;display:grid;width:34px;height:34px;padding:0;place-items:center;color:#38251d;background:#fff;border:1px solid #d9c6b8;border-radius:50%;font-size:22px;line-height:1;cursor:pointer}
      body.achanbay-affiliate-banner-open{padding-bottom:124px}
      @media(max-width:640px){.achanbay-affiliate-banner{right:8px;bottom:8px;left:8px;min-height:0;gap:10px;padding:10px 42px 10px 10px;border-radius:14px}.achanbay-affiliate-banner__image{flex-basis:54px;width:54px;height:54px}.achanbay-affiliate-banner__title{font-size:14px}.achanbay-affiliate-banner__description{display:none}.achanbay-affiliate-banner__cta{min-height:38px;padding:6px 10px;font-size:11px}body.achanbay-affiliate-banner-open{padding-bottom:108px}}
      @media(prefers-reduced-motion:reduce){.achanbay-affiliate-banner{transition:none}}
    `;
    document.head.appendChild(style);
  }

  function render(ad, config, ctx) {
    addStyles();

    const banner = document.createElement("aside");
    banner.className = "achanbay-affiliate-banner";
    banner.setAttribute("role", "complementary");
    banner.setAttribute("aria-label", ad.sponsored === false ? "おすすめ情報" : "広告");

    if (ad.imageUrl) {
      const image = document.createElement("img");
      image.className = "achanbay-affiliate-banner__image";
      image.src = ad.imageUrl;
      image.alt = "";
      image.width = 68;
      image.height = 68;
      image.loading = "lazy";
      image.addEventListener("error", () => image.remove());
      banner.appendChild(image);
    }

    const body = document.createElement("div");
    body.className = "achanbay-affiliate-banner__body";

    const label = document.createElement("span");
    label.className = "achanbay-affiliate-banner__label";
    label.textContent = ad.label || (ad.sponsored === false ? "おすすめ" : "PR");

    const title = document.createElement("strong");
    title.className = "achanbay-affiliate-banner__title";
    title.textContent = ad.title || "おすすめ情報";

    body.append(label, title);

    if (ad.description) {
      const description = document.createElement("p");
      description.className = "achanbay-affiliate-banner__description";
      description.textContent = ad.description;
      body.appendChild(description);
    }

    const link = document.createElement("a");
    link.className = "achanbay-affiliate-banner__cta";
    link.href = ad.destinationUrl;
    link.target = "_blank";
    link.rel = ad.sponsored === false ? "noopener noreferrer" : "sponsored nofollow noopener noreferrer";
    link.textContent = ad.cta || "詳しく見る";
    link.addEventListener("click", () => track("affiliate_banner_click", ad, ctx));

    const close = document.createElement("button");
    close.className = "achanbay-affiliate-banner__close";
    close.type = "button";
    close.setAttribute("aria-label", "バナーを閉じる");
    close.textContent = "×";
    close.addEventListener("click", () => {
      const hours = Math.max(0, Number(config.dismissHours || 24));
      storage.set("dismissed_until", now + hours * 60 * 60 * 1000);
      banner.classList.remove("is-visible");
      document.body.classList.remove("achanbay-affiliate-banner-open");
      window.setTimeout(() => banner.remove(), 320);
    });

    banner.append(body, link, close);
    document.body.appendChild(banner);
    document.body.classList.add("achanbay-affiliate-banner-open");
    window.requestAnimationFrame(() => banner.classList.add("is-visible"));

    storage.set("impression_" + ad.id, now);
    storage.set("global_impression", now);
    track("affiliate_banner_impression", ad, ctx);
  }

  async function initialise() {
    if (Number(storage.get("dismissed_until") || 0) > now) return;

    try {
      const response = await fetch(configUrl, { credentials: "same-origin", cache: "no-cache" });
      if (!response.ok) throw new Error("Banner config request failed");
      const config = await response.json();
      if (!config.enabled || !Array.isArray(config.ads)) return;

      const globalHours = Math.max(0, Number(config.globalFrequencyCapHours || 0));
      const lastGlobalImpression = Number(storage.get("global_impression") || 0);
      if (globalHours && lastGlobalImpression && now - lastGlobalImpression < globalHours * 60 * 60 * 1000) return;

      const ctx = context(config);
      const eligible = config.ads.filter(ad => isEligible(ad, ctx));
      if (!eligible.length) return;

      const ad = chooseAd(eligible, config.selectionTtlHours, ctx);
      const delay = Math.max(0, Number(config.displayDelayMs || 0));
      window.setTimeout(() => render(ad, config, ctx), delay);
    } catch (error) {
      if (window.console) console.warn("[Achanbay banner]", error);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialise, { once: true });
  } else {
    initialise();
  }
})();
