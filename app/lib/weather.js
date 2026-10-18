// Weather from Open-Meteo (free, no key). Cached for 15 minutes.
const { WMO } = require("./ai");

class Weather {
  constructor(cfg = {}, { fetchFn = globalThis.fetch, now = () => Date.now() } = {}) {
    this.cfg = { place: "San Jose", lat: 37.3382, lon: -121.8863, units: "imperial", ...cfg };
    this.fetch = fetchFn; this.now = now; this.last = null;
  }
  async get() {
    if (this.last && this.now() - this.last.t < 15 * 60 * 1000) return this.last.w;
    const c = this.cfg, f = c.units === "imperial";
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${c.lat}&longitude=${c.lon}` +
      `&current=temperature_2m,weather_code,wind_speed_10m&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max` +
      `&timezone=auto&forecast_days=1${f ? "&temperature_unit=fahrenheit&wind_speed_unit=mph" : ""}`;
    try {
      const r = await Promise.race([this.fetch(url), new Promise((_, rej) => setTimeout(() => rej(new Error("timed out")), 6000))]);
      const w = parse(await r.json(), c);
      if (w) this.last = { t: this.now(), w };
      return w;
    } catch (e) { return this.last ? this.last.w : null; }
  }
}

function parse(j, c) {
  if (!j || !j.current) return null;
  const code = j.current.weather_code;
  return {
    place: c.place, unit: c.units === "imperial" ? "°F" : "°C",
    temp: Math.round(j.current.temperature_2m), code, desc: WMO[code] || "",
    wind: Math.round(j.current.wind_speed_10m),
    high: Math.round(j.daily?.temperature_2m_max?.[0]), low: Math.round(j.daily?.temperature_2m_min?.[0]),
    rain: j.daily?.precipitation_probability_max?.[0] ?? 0
  };
}

module.exports = { Weather, parseWeather: parse };
