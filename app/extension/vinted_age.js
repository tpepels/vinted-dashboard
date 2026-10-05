/* Pure Vinted "Uploaded" age parsing.

Loaded before content.js in the same isolated content-script world. This module
contains no Chrome API calls and no network/navigation logic.
*/
globalThis.VintedAge = (() => {
  const UPLOADED_LABELS = [
    "uploaded",
    "carregado",
    "publicado",
    "subido",
    "televerse",
    "mis en ligne",
    "caricato",
    "hochgeladen",
    "geupload",
    "dodano",
  ];

  const RELATIVE_UNITS = [
    [60, ["minute", "minutes", "minuto", "minutos", "minuti", "minuten"]],
    [3600, ["hour", "hours", "hora", "horas", "heure", "heures", "ora", "ore", "stunde", "stunden", "uur", "uren"]],
    [86400, ["day", "days", "dia", "dias", "jour", "jours", "giorno", "giorni", "tag", "tage", "tagen", "dag", "dagen"]],
    [604800, ["week", "weeks", "semana", "semanas", "semaine", "semaines", "settimana", "settimane", "woche", "wochen", "weken"]],
    [2630016, ["month", "months", "mes", "meses", "mois", "mese", "mesi", "monat", "monate", "monaten", "maand", "maanden"]],
    [31557600, ["year", "years", "ano", "anos", "an", "ans", "anno", "anni", "jahr", "jahre", "jahren", "jaar", "jaren"]],
  ];

  const UNIT_PATTERN = RELATIVE_UNITS.flatMap(([, names]) => names).join("|");

  function normalize(value) {
    return String(value || "")
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function secondsFromRelative(value) {
    if (value == null || value === "") return null;
    let text = normalize(value);
    if (!text) return null;

    if (/^(today|hoje|hoy|oggi|heute|vandaag|aujourd.?hui)$/.test(text)) {
      return 0;
    }
    if (/^(yesterday|ontem|ayer|ieri|gestern|gisteren|hier)$/.test(text)) {
      return 86400;
    }

    text = text.replace(
      /\b(a|an|one|um|uma|un|una|uno|une|ein|eine|einem|einer|een)\b/g,
      "1",
    );
    const match = text.match(/(\d+(?:[.,]\d+)?)\s*([a-z]+)/);
    if (!match) return null;

    const amount = Number(match[1].replace(",", "."));
    if (!Number.isFinite(amount) || amount < 0) return null;

    const unit = match[2];
    for (const [seconds, names] of RELATIVE_UNITS) {
      if (names.includes(unit)) return Math.round(amount * seconds);
    }
    return null;
  }

  function relativePhrase(value) {
    const text = normalize(value);
    if (!text) return null;

    for (const single of [
      "today",
      "hoje",
      "hoy",
      "oggi",
      "heute",
      "vandaag",
      "aujourd'hui",
      "yesterday",
      "ontem",
      "ayer",
      "ieri",
      "gestern",
      "gisteren",
      "hier",
    ]) {
      if (text.startsWith(single) || text.includes(" " + single + " ")) {
        return single;
      }
    }

    const pattern = new RegExp(
      "(?:\\b(?:a|an|one|um|uma|un|una|uno|une|ein|eine|einem|einer|een|\\d+(?:[.,]\\d+)?)\\s+(?:" +
        UNIT_PATTERN +
        ")\\b(?:\\s+(?:ago|atras|atrás))?)",
      "i",
    );
    const match = text.match(pattern);
    return match ? match[0] : null;
  }

  function fromUploadedText(value) {
    const text = normalize(value);
    if (!text) return null;

    for (const label of UPLOADED_LABELS) {
      let at = text.indexOf(label);
      while (at >= 0) {
        const tail = text.slice(at + label.length, at + label.length + 220);
        const phrase = relativePhrase(tail);
        const seconds = secondsFromRelative(phrase);
        if (seconds != null) return { seconds, text: phrase };
        at = text.indexOf(label, at + label.length);
      }
    }
    return null;
  }

  function fromRenderedDocument(doc = document) {
    const text = doc?.body?.innerText || doc?.body?.textContent || "";
    return fromUploadedText(text);
  }

  function advanceCached(cached, nowSeconds = Date.now() / 1000) {
    if (!cached || cached.listed_age_seconds == null) return null;
    const base = Number(cached.listed_age_seconds);
    if (!Number.isFinite(base) || base < 0) return null;

    const observed = Number(cached.age_observed_at || 0);
    const elapsed = observed > 0 ? Math.max(0, nowSeconds - observed) : 0;
    return Math.round(base + elapsed);
  }

  return {
    normalize,
    secondsFromRelative,
    fromUploadedText,
    fromRenderedDocument,
    advanceCached,
  };
})();
