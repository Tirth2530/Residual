// News for someone who can't hold a phone: RSS feeds (no keys, no tracking), grouped into a few
// big sections, plus "For you" from the person's own interests. Articles are fetched as text so
// Residual can read them aloud or summarise them.

const DEFAULT_SECTIONS = [
  { id: "top", name: "Top stories", feeds: ["https://feeds.bbci.co.uk/news/rss.xml", "https://feeds.npr.org/1001/rss.xml"] },
  { id: "world", name: "World", feeds: ["https://feeds.bbci.co.uk/news/world/rss.xml"] },
  { id: "sport", name: "Sport", feeds: ["https://feeds.bbci.co.uk/sport/rss.xml", "https://www.espn.com/espn/rss/news"] },
  { id: "tech", name: "Tech & science", feeds: ["https://feeds.bbci.co.uk/news/technology/rss.xml", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"] },
  { id: "india", name: "India", feeds: ["https://feeds.bbci.co.uk/news/world/asia/india/rss.xml"] }
];
const googleSearch = q => `https://news.google.com/rss/search?q=${encodeURIComponent(q)}&hl=en-US&gl=US&ceid=US:en`;

class News {
  constructor(cfg = {}, { fetchFn = globalThis.fetch, now = () => Date.now() } = {}) {
    this.sections = cfg.sections || DEFAULT_SECTIONS;
    this.interests = cfg.interests || [];
    this.fetch = fetchFn; this.now = now;
    this.cache = new Map();   // url -> {t, items}
    this.ttl = (cfg.refreshMin || 10) * 60 * 1000;
  }

  list() {
    const s = this.interests.length ? [{ id: "foryou", name: "For you", feeds: this.interests.map(googleSearch), interests: this.interests }] : [];
    return [...s, ...this.sections].map(x => ({ id: x.id, name: x.name, interests: x.interests }));
  }

  async section(id, limit = 12) {
    const all = [{ id: "foryou", feeds: this.interests.map(googleSearch) }, ...this.sections];
    const sec = all.find(s => s.id === id) || this.sections[0];
    const results = await Promise.all(sec.feeds.map(u => this.feed(u).catch(() => [])));
    // interleave feeds so one source doesn't take over, drop duplicate headlines
    const seen = new Set(), out = [];
    for (let i = 0; out.length < limit && results.some(r => r[i]); i++) {
      for (const r of results) {
        const it = r[i]; if (!it) continue;
        const key = it.title.toLowerCase().replace(/\W+/g, " ").slice(0, 60);
        if (seen.has(key)) continue;
        seen.add(key); out.push(it);
      }
    }
    return out.slice(0, limit);
  }

  async feed(url) {
    const c = this.cache.get(url);
    if (c && this.now() - c.t < this.ttl) return c.items;
    const r = await withTimeout(this.fetch(url, { headers: { "User-Agent": "Residual/1.0 (+news reader)" } }), 8000);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const items = parseFeed(await r.text());
    this.cache.set(url, { t: this.now(), items });
    return items;
  }

  // Readable text of an article: paragraphs only.
  async article(url) {
    try {
      const r = await withTimeout(this.fetch(url, { headers: { "User-Agent": "Mozilla/5.0 Residual" }, redirect: "follow" }), 9000);
      return extractText(await r.text());
    } catch (e) { return ""; }
  }
}

// ---------------------------------------------------------------- parsing (no dependencies)
function parseFeed(xml) {
  const items = [];
  const blocks = xml.match(/<item[\s>][\s\S]*?<\/item>/gi) || xml.match(/<entry[\s>][\s\S]*?<\/entry>/gi) || [];
  const chan = decode(tag(xml.split(/<item[\s>]|<entry[\s>]/i)[0], "title"));
  for (const b of blocks) {
    let title = decode(tag(b, "title"));
    let link = decode(tag(b, "link")) || (b.match(/<link[^>]*href="([^"]+)"/i) || [])[1] || "";
    let source = decode(tag(b, "source")) || chan.replace(/^BBC News.*$/, "BBC News").replace(/\s*-\s*.*$/, "") || "";
    // Google News titles end in " - Publisher"
    const m = title.match(/^(.*)\s-\s([^-]{2,40})$/);
    if (m && /news\.google/.test(link)) { title = m[1]; source = source || m[2]; }
    const desc = stripTags(decode(tag(b, "description") || tag(b, "summary") || tag(b, "content")));
    const when = Date.parse(tag(b, "pubDate") || tag(b, "updated") || tag(b, "published") || "") || null;
    const img = (b.match(/<media:thumbnail[^>]*url="([^"]+)"/i) || b.match(/<media:content[^>]*url="([^"]+)"/i) || b.match(/<enclosure[^>]*url="([^"]+)"[^>]*type="image/i) || [])[1] || null;
    if (title && link) items.push({ title: title.trim(), link: link.trim(), source: source.trim() || host(link), desc: desc.slice(0, 400), when, img });
  }
  return items;
}
function tag(s, name) {
  const m = s.match(new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${name}>`, "i"));
  if (!m) return "";
  return m[1].replace(/^\s*<!\[CDATA\[([\s\S]*?)\]\]>\s*$/, "$1").trim();
}
const ENT = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", rsquo: "’", lsquo: "‘", rdquo: "”", ldquo: "“", mdash: "\u2014", ndash: "\u2013", hellip: "…" };
function decode(s) {
  return String(s || "").replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(+d))
    .replace(/&([a-z]+);/gi, (m, n) => ENT[n.toLowerCase()] ?? m);
}
function stripTags(s) { return String(s || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(); }
function host(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (e) { return ""; } }
function extractText(html) {
  const body = (html.match(/<article[\s\S]*?<\/article>/i) || [html])[0]
    .replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>|<figure[\s\S]*?<\/figure>|<nav[\s\S]*?<\/nav>/gi, " ");
  const ps = (body.match(/<p[\s>][\s\S]*?<\/p>/gi) || []).map(p => decode(stripTags(p))).filter(p => p.length > 60 && !/cookie|subscribe|sign up|newsletter|©/i.test(p));
  return ps.join("\n").slice(0, 8000);
}
function withTimeout(p, ms) { return Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error("timed out")), ms))]); }

module.exports = { News, parseFeed, extractText, DEFAULT_SECTIONS };
