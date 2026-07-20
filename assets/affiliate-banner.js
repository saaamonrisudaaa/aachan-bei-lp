(() => {
  "use strict";

  if (window.__achanbayAffiliateBannerLoaded) return;
  window.__achanbayAffiliateBannerLoaded = true;

  const loader = document.currentScript;
  const configUrl = loader?.dataset.config || "./assets/affiliate-banners.json";
  const prefix = "achanbay_affiliate_banner_";
  const now = Date.now();

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
    if ((start !== null && !Number.isFinite(start)) || (end !== null && !Number.isFinite(end))) return false;
    return (start === null || start <= now) && (end === null || end >= now);
  }

  function isFrequencyCapped(ad, placement) {
    const hours = Number(ad.frequencyCapHours || 0);
    if (!hours) return false;
    const lastShown = Number(storage.get("impression_" + placement + "_" + ad.id) || 0);
    return Boolean(lastShown && now - lastShown < hours * 60 * 60 * 1000);
  }

  function isAllowedInPlacement(ad, config, placement) {
    const adIds = config.placements?.[placement]?.adIds;
    return !Array.isArray(adIds) || !adIds.length || adIds.includes(ad.id);
  }

  function isEligible(ad, config, ctx, placement) {
    if (!ad || ad.active === false || !ad.id || !ad.destinationUrl) return false;
    if (!isAllowedInPlacement(ad, config, placement)) return false;
    if (!isWithinSchedule(ad) || isFrequencyCapped(ad, placement)) return false;
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

  function adWeight(ad) {
    const weight = Number(ad.weight);
    return Number.isFinite(weight) ? Math.max(0, weight) : 1;
  }

  function chooseAd(ads, ttlHours, ctx, placement) {
    const bestScore = Math.max(...ads.map(ad => relevanceScore(ad, ctx)));
    const candidates = ads.filter(ad => relevanceScore(ad, ctx) === bestScore);
    const storedId = storage.get("selected_" + placement + "_id");
    const selectedAt = Number(storage.get("selected_" + placement + "_at") || 0);
    const ttl = Math.max(0, Number(ttlHours || 0)) * 60 * 60 * 1000;
    const stored = candidates.find(ad => ad.id === storedId);
    if (stored && (!ttl || now - selectedAt < ttl)) return stored;

    const total = candidates.reduce((sum, ad) => sum + adWeight(ad), 0);
    let cursor = Math.random() * (total || candidates.length);
    const selected = candidates.find(ad => {
      cursor -= total ? adWeight(ad) : 1;
      return cursor <= 0;
    }) || candidates[0];

    storage.set("selected_" + placement + "_id", selected.id);
    storage.set("selected_" + placement + "_at", now);
    return selected;
  }

  function track(eventName, ad, ctx, placement) {
    const parameters = {
      ad_id: ad.id,
      ad_placement: placement,
      asp: ad.asp || "unknown",
      page_category: ctx.category,
      device_type: ctx.device,
      traffic_source: ctx.source,
      visitor_type: ctx.visitorType
    };
    if (typeof window.gtag === "function") {
      window.gtag("event", eventName, parameters);
    } else {
      window.dataLayer = window.dataLayer || [];
      window.dataLayer.push({ event: eventName, ...parameters });
    }
  }

  function addStyles() {
    if (document.getElementById("achanbay-affiliate-banner-style")) return;
    const style = document.createElement("style");
    style.id = "achanbay-affiliate-banner-style";
    style.textContent = `
      .achanbay-affiliate-card{box-sizing:border-box;display:grid;min-width:0;align-items:center;gap:14px;color:#38251d;background:#fff8ed;border:1px solid #edc9a8;border-radius:14px;box-shadow:0 10px 24px rgba(89,54,28,.09);font-family:"Noto Sans JP",sans-serif;text-align:left}
      .achanbay-affiliate-card__image{display:block;width:100%;height:auto;aspect-ratio:1/1;object-fit:contain;background:#fff;border:1px solid #ead8c7;border-radius:10px}
      .achanbay-affiliate-card__body{min-width:0}
      .achanbay-affiliate-card__label{display:inline-flex;margin-bottom:5px;padding:3px 8px;color:#8e294c;background:#ffe5ee;border-radius:999px;font-size:10px;font-weight:800;letter-spacing:.04em}
      .achanbay-affiliate-card__title{display:block;margin:0;color:#38251d;font-size:16px;font-weight:800;line-height:1.45}
      .achanbay-affiliate-card__description{margin:5px 0 0;color:#775d51;font-family:"Noto Sans JP",sans-serif;font-size:12px;font-weight:400;line-height:1.65}
      .achanbay-affiliate-card__cta{display:inline-flex;align-items:center;justify-content:center;min-height:42px;padding:9px 14px;color:#fff;background:#bf0000;border:2px solid #38251d;border-radius:999px;box-shadow:2px 3px 0 #38251d;font-size:12px;font-weight:800;text-decoration:none;white-space:nowrap}
      .achanbay-affiliate-card__cta:hover,.achanbay-affiliate-card__cta:focus-visible{color:#fff;background:#a90000;transform:translateY(-1px)}
      .achanbay-affiliate-card--sidebar{grid-template-columns:1fr;margin-top:24px;padding:16px}
      .achanbay-affiliate-card--sidebar.has-image{grid-template-columns:64px minmax(0,1fr)}
      .achanbay-affiliate-card--sidebar .achanbay-affiliate-card__cta{grid-column:1/-1;width:100%}
      .seo-facts.achanbay-affiliate-host,.shop-info.achanbay-affiliate-host{position:static}
      .achanbay-affiliate-card--footer{grid-template-columns:minmax(0,1fr) auto;width:min(100%,1120px);margin:0 auto 30px;padding:16px}
      .achanbay-affiliate-card--footer.has-image{grid-template-columns:76px minmax(0,1fr) auto}
      .site-footer .achanbay-affiliate-card__description{margin:5px 0 0;color:#775d51;font-family:"Noto Sans JP",sans-serif;font-size:12px;font-weight:400}
      .site-footer .achanbay-affiliate-card__cta{color:#fff;font-size:12px}
      @media(max-width:860px){.achanbay-affiliate-card--sidebar{display:none}}
      @media(max-width:760px){
        .achanbay-affiliate-card--footer{grid-template-columns:1fr;margin-bottom:22px;padding:14px}
        .achanbay-affiliate-card--footer.has-image{grid-template-columns:58px minmax(0,1fr)}
        .achanbay-affiliate-card--footer .achanbay-affiliate-card__cta{grid-column:1/-1;width:100%}
        .achanbay-affiliate-card--sidebar{grid-template-columns:1fr;padding:14px}
        .achanbay-affiliate-card--sidebar.has-image{grid-template-columns:58px minmax(0,1fr)}
        .achanbay-affiliate-card__title{font-size:14px}
        .achanbay-affiliate-card__description{font-size:11px}
      }
    `;
    document.head.appendChild(style);
  }

  function createCard(ad, ctx, placement) {
    const card = document.createElement("div");
    card.className = "achanbay-affiliate-card achanbay-affiliate-card--" + placement;
    card.dataset.adId = ad.id;
    card.dataset.adPlacement = placement;
    card.setAttribute("role", "region");
    const placementLabel = placement === "sidebar" ? "記事サイドバー" : "フッター";
    card.setAttribute("aria-label", ad.sponsored === false ? placementLabel + "のおすすめ情報" : placementLabel + "広告");

    if (ad.imageUrl) {
      const image = document.createElement("img");
      image.className = "achanbay-affiliate-card__image";
      image.src = ad.imageUrl;
      image.alt = "";
      image.width = placement === "sidebar" ? 64 : 76;
      image.height = placement === "sidebar" ? 64 : 76;
      image.loading = "lazy";
      image.decoding = "async";
      card.classList.add("has-image");
      image.addEventListener("error", () => {
        card.classList.remove("has-image");
        image.remove();
      });
      card.appendChild(image);
    }

    const body = document.createElement("div");
    body.className = "achanbay-affiliate-card__body";

    const label = document.createElement("span");
    label.className = "achanbay-affiliate-card__label";
    label.textContent = ad.label || (ad.sponsored === false ? "おすすめ" : "広告・PR");

    const title = document.createElement("strong");
    title.className = "achanbay-affiliate-card__title";
    title.textContent = ad.title || "おすすめ情報";
    body.append(label, title);

    if (ad.description) {
      const description = document.createElement("p");
      description.className = "achanbay-affiliate-card__description";
      description.textContent = ad.description;
      body.appendChild(description);
    }

    const link = document.createElement("a");
    link.className = "achanbay-affiliate-card__cta";
    link.href = ad.destinationUrl;
    link.target = "_blank";
    link.rel = ad.sponsored === false ? "noopener noreferrer" : "sponsored nofollow noopener noreferrer";
    link.textContent = ad.cta || "詳しく見る";
    link.addEventListener("click", () => track("affiliate_banner_click", ad, ctx, placement));

    card.append(body, link);
    return card;
  }

  function placementTarget(placement) {
    if (placement === "sidebar") return document.querySelector(".seo-facts, .shop-info");
    if (placement === "footer") return document.querySelector(".site-footer");
    return null;
  }

  function renderPlacement(ad, ctx, placement) {
    const target = placementTarget(placement);
    if (!target) return false;
    addStyles();
    const card = createCard(ad, ctx, placement);

    if (placement === "sidebar") {
      target.classList.add("achanbay-affiliate-host");
      target.appendChild(card);
    } else {
      target.insertBefore(card, target.firstChild);
    }

    const recordImpression = () => {
      const viewedAt = Date.now();
      storage.set("impression_" + placement + "_" + ad.id, viewedAt);
      storage.set("global_impression_" + placement, viewedAt);
      track("affiliate_banner_impression", ad, ctx, placement);
    };

    if ("IntersectionObserver" in window) {
      const observer = new window.IntersectionObserver(entries => {
        if (!entries.some(entry => entry.isIntersecting && entry.intersectionRatio >= 0.25)) return;
        observer.disconnect();
        recordImpression();
      }, { threshold: [0.25] });
      observer.observe(card);
    } else {
      recordImpression();
    }
    return true;
  }

  function isPlacementCapped(config, placement) {
    const hours = Math.max(0, Number(config.globalFrequencyCapHours || 0));
    const lastImpression = Number(storage.get("global_impression_" + placement) || 0);
    return Boolean(hours && lastImpression && now - lastImpression < hours * 60 * 60 * 1000);
  }

  async function initialise() {
    try {
      const response = await fetch(configUrl, { credentials: "same-origin", cache: "no-cache" });
      if (!response.ok) throw new Error("Banner config request failed");
      const config = await response.json();
      if (!config.enabled || !Array.isArray(config.ads)) return;

      const ctx = context(config);
      const usedIds = new Set();
      const placements = window.matchMedia("(max-width: 860px)").matches
        ? ["footer"]
        : ["sidebar", "footer"];

      const display = () => {
        placements.forEach(placement => {
          if (!placementTarget(placement) || isPlacementCapped(config, placement)) return;
          const eligible = config.ads.filter(ad => isEligible(ad, config, ctx, placement));
          const candidates = eligible.filter(ad => !usedIds.has(ad.id));
          if (!candidates.length) return;
          const ad = chooseAd(candidates, config.selectionTtlHours, ctx, placement);
          if (renderPlacement(ad, ctx, placement)) usedIds.add(ad.id);
        });
      };

      const delay = Math.max(0, Number(config.displayDelayMs || 0));
      if (delay) window.setTimeout(display, delay);
      else display();
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
