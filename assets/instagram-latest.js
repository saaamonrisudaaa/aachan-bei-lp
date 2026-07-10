(() => {
  "use strict";

  const grid = document.querySelector(".instagram-link-grid");
  if (!grid) return;

  const titleFromCaption = (caption, index) => {
    const firstLine = String(caption || "")
      .split(/\r?\n/)
      .map(line => line.trim())
      .find(line => line && !line.startsWith("#"));
    if (!firstLine) return `Instagramで店舗投稿を見る`;
    const cleaned = firstLine.replace(/\s*#.*$/, "").trim();
    return cleaned.length > 46 ? cleaned.slice(0, 45) + "…" : cleaned;
  };

  const render = posts => {
    const latest = posts.filter(post => post && post.url).slice(0, 3);
    if (!latest.length) return;

    const fragment = document.createDocumentFragment();
    latest.forEach((post, index) => {
      const link = document.createElement("a");
      link.className = "instagram-post-link";
      link.href = post.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", `Instagramの最新投稿${index + 1}を見る`);

      const number = document.createElement("span");
      number.textContent = `最新投稿 ${String(index + 1).padStart(2, "0")}`;

      const title = document.createElement("strong");
      title.textContent = titleFromCaption(post.caption, index);

      link.append(number, title);
      fragment.appendChild(link);
    });

    grid.replaceChildren(fragment);
  };

  fetch(`./assets/instagram-posts.json?v=${Date.now()}`, {
    cache: "no-store",
    credentials: "same-origin"
  })
    .then(response => {
      if (!response.ok) throw new Error("Instagram feed request failed");
      return response.json();
    })
    .then(data => {
      if (Array.isArray(data.posts)) render(data.posts);
    })
    .catch(() => {
      // Keep the HTML fallback links when the feed cannot be refreshed.
    });
})();
