const API = "/api/v1";
const displayLocale = safeLocale(navigator.language);

const dom = {
  brandName: document.querySelector("#brandName"),
  footerName: document.querySelector("#footerName"),
  catalogStatus: document.querySelector("#catalogStatus"),
  dateHeading: document.querySelector("#dateHeading"),
  dateDisplay: document.querySelector("#dateDisplay"),
  dateSubheading: document.querySelector("#dateSubheading"),
  dateDisplayButton: document.querySelector("#dateDisplayButton"),
  datePicker: document.querySelector("#datePicker"),
  previousButton: document.querySelector("#previousButton"),
  nextButton: document.querySelector("#nextButton"),
  todayHeaderButton: document.querySelector("#todayHeaderButton"),
  rangeChips: [...document.querySelectorAll(".range-chip")],
  customRangeForm: document.querySelector("#customRangeForm"),
  customFrom: document.querySelector("#customFrom"),
  customTo: document.querySelector("#customTo"),
  resultCount: document.querySelector("#resultCount"),
  countryCount: document.querySelector("#countryCount"),
  sourceCount: document.querySelector("#sourceCount"),
  conflictCount: document.querySelector("#conflictCount"),
  resultsKicker: document.querySelector("#resultsKicker"),
  resultsTitle: document.querySelector("#resultsTitle"),
  results: document.querySelector("#results"),
  loadingState: document.querySelector("#loadingState"),
  loadMoreButton: document.querySelector("#loadMoreButton"),
  filtersPanel: document.querySelector("#filtersPanel"),
  filterScrim: document.querySelector("#filterScrim"),
  mobileFilterButton: document.querySelector("#mobileFilterButton"),
  closeFiltersButton: document.querySelector("#closeFiltersButton"),
  clearFiltersButton: document.querySelector("#clearFiltersButton"),
  searchInput: document.querySelector("#searchInput"),
  countryPicker: document.querySelector("#countryPicker"),
  countryFilterSummary: document.querySelector("#countryFilterSummary"),
  countryFilterOptions: document.querySelector("#countryFilterOptions"),
  languageFilter: document.querySelector("#languageFilter"),
  networkFilter: document.querySelector("#networkFilter"),
  genreFilter: document.querySelector("#genreFilter"),
  formatFilter: document.querySelector("#formatFilter"),
  sourceFilter: document.querySelector("#sourceFilter"),
  eventTypeFilter: document.querySelector("#eventTypeFilter"),
  confidenceFilter: document.querySelector("#confidenceFilter"),
  conflictInputs: [...document.querySelectorAll('input[name="conflict"]')],
  sortSelect: document.querySelector("#sortSelect"),
  activeFilters: document.querySelector("#activeFilters"),
  calendarPanel: document.querySelector("#calendarPanel"),
  calendarGrid: document.querySelector("#calendarGrid"),
  detailDialog: document.querySelector("#detailDialog"),
  detailContent: document.querySelector("#detailContent"),
  detailCloseButton: document.querySelector("#detailCloseButton"),
  creditsButton: document.querySelector("#creditsButton"),
  creditsDialog: document.querySelector("#creditsDialog"),
  creditsCloseButton: document.querySelector("#creditsCloseButton"),
  creditsSources: document.querySelector("#creditsSources"),
  coverageButton: document.querySelector("#coverageButton"),
  coverageDialog: document.querySelector("#coverageDialog"),
  coverageCloseButton: document.querySelector("#coverageCloseButton"),
  coverageContent: document.querySelector("#coverageContent"),
  themeButton: document.querySelector("#themeButton"),
  toast: document.querySelector("#toast"),
};

const today = localISODate(new Date());
const tomorrow = isoDate(addDays(parseISO(today), 1));
const initial = new URLSearchParams(location.search);

const state = {
  view: validView(initial.get("view")) || "day",
  anchor: validDate(initial.get("date")) || today,
  customFrom: validDate(initial.get("from")) || today,
  customTo: validDate(initial.get("to")) || today,
  filters: {
    q: initial.get("q") || "",
    country: initial.get("country") || "",
    language: initial.get("language") || "",
    network: initial.get("network") || "",
    genre: initial.get("genre") || "",
    format: initial.get("format") || "",
    source: initial.get("source") || "",
    event_type: initial.get("event_type") || "",
    confidence: initial.get("confidence") || "",
    conflict: initial.get("conflict") || "",
  },
  sort: validSort(initial.get("sort")) || "date_asc",
  offset: 0,
  limit: 60,
  items: [],
  pagination: null,
  summary: null,
  meta: null,
  facets: null,
  credits: null,
  loading: false,
  controller: null,
};

boot();

async function boot() {
  restoreTheme();
  bindEvents();
  syncControlsFromState();
  updateDatePresentation();

  try {
    const meta = await getJSON(`${API}/meta`);
    state.meta = meta;
    applyMeta(meta);
  } catch (error) {
    dom.catalogStatus.textContent = navigator.onLine
      ? "Catalog API unavailable"
      : "Offline";
  }

  await refresh({ reset: true });

  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

function bindEvents() {
  dom.previousButton.addEventListener("click", () => shiftPeriod(-1));
  dom.nextButton.addEventListener("click", () => shiftPeriod(1));
  dom.todayHeaderButton.addEventListener("click", () => {
    state.anchor = today;
    state.view = "day";
    refresh({ reset: true });
  });

  dom.dateDisplayButton.addEventListener("click", () => {
    dom.datePicker.value = state.anchor;
    if (typeof dom.datePicker.showPicker === "function") {
      dom.datePicker.showPicker();
    } else {
      dom.datePicker.click();
    }
  });

  dom.datePicker.addEventListener("change", () => {
    if (validDate(dom.datePicker.value)) {
      state.anchor = dom.datePicker.value;
      if (state.view === "custom") state.view = "day";
      refresh({ reset: true });
    }
  });

  dom.rangeChips.forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.dataset.view;
      if (button.dataset.relative === "today") state.anchor = today;
      if (button.dataset.relative === "tomorrow") state.anchor = tomorrow;
      if (state.view === "custom") {
        const range = currentRange();
        state.customFrom = range.from;
        state.customTo = range.to;
        dom.customFrom.value = state.customFrom;
        dom.customTo.value = state.customTo;
        dom.customRangeForm.hidden = false;
        syncViewChips();
        return;
      }
      dom.customRangeForm.hidden = true;
      refresh({ reset: true });
    });
  });

  dom.customRangeForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!validDate(dom.customFrom.value) || !validDate(dom.customTo.value)) return;
    if (dom.customTo.value < dom.customFrom.value) {
      showToast("The end date must be after the start date.");
      return;
    }
    state.customFrom = dom.customFrom.value;
    state.customTo = dom.customTo.value;
    state.anchor = state.customFrom;
    state.view = "custom";
    refresh({ reset: true });
  });

  const filterBindings = [
    [dom.languageFilter, "language"],
    [dom.networkFilter, "network"],
    [dom.genreFilter, "genre"],
    [dom.formatFilter, "format"],
    [dom.sourceFilter, "source"],
    [dom.eventTypeFilter, "event_type"],
    [dom.confidenceFilter, "confidence"],
  ];
  filterBindings.forEach(([control, key]) => {
    const updateFilter = () => {
      const next = control.value;
      if (state.filters[key] === next) return;
      state.filters[key] = next;
      refresh({ reset: true });
    };
    control.addEventListener("change", updateFilter);
  });

  dom.conflictInputs.forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) {
        state.filters.conflict = input.value;
        refresh({ reset: true });
      }
    });
  });

  let searchTimer;
  dom.searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.filters.q = dom.searchInput.value.trim();
      refresh({ reset: true });
    }, 260);
  });

  dom.clearFiltersButton.addEventListener("click", clearFilters);
  dom.sortSelect.addEventListener("change", () => {
    state.sort = dom.sortSelect.value;
    refresh({ reset: true });
  });

  dom.loadMoreButton.addEventListener("click", () => refresh({ reset: false }));

  dom.mobileFilterButton.addEventListener("click", openFilters);
  dom.closeFiltersButton.addEventListener("click", closeFilters);
  dom.filterScrim.addEventListener("click", closeFilters);
  document.addEventListener("click", (event) => {
    if (!dom.countryPicker.contains(event.target)) {
      dom.countryPicker.removeAttribute("open");
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && dom.filtersPanel.classList.contains("is-open")) {
      closeFilters();
      dom.mobileFilterButton.focus();
    }
  });

  document.addEventListener("htmx:response:error", (event) => {
    const target = event.detail?.ctx?.target;
    if (target === dom.detailContent || target === dom.creditsSources || target === dom.coverageContent) {
      target.replaceChildren(
        element("div", { className: "error-state" }, [
          element("strong", {}, "This content could not be loaded."),
          element("p", {}, "Please close this panel and try again."),
        ]),
      );
    }
  });

  dom.detailCloseButton.addEventListener("click", () => dom.detailDialog.close());
  dom.creditsButton.addEventListener("click", () => dom.creditsDialog.showModal());
  dom.creditsCloseButton.addEventListener("click", () => dom.creditsDialog.close());
  dom.coverageButton.addEventListener("click", () => dom.coverageDialog.showModal());
  dom.coverageCloseButton.addEventListener("click", () => dom.coverageDialog.close());
  dom.themeButton.addEventListener("click", cycleTheme);

  window.addEventListener("popstate", () => {
    const params = new URLSearchParams(location.search);
    state.view = validView(params.get("view")) || "day";
    state.anchor = validDate(params.get("date")) || today;
    state.customFrom = validDate(params.get("from")) || state.anchor;
    state.customTo = validDate(params.get("to")) || state.anchor;
    Object.keys(state.filters).forEach((key) => {
      state.filters[key] = params.get(key) || "";
    });
    state.sort = validSort(params.get("sort")) || "date_asc";
    syncControlsFromState();
    refresh({ reset: true, history: false });
  });
}

async function refresh({ reset, history = true }) {
  if (reset) {
    state.offset = 0;
    state.items = [];
  }

  if (state.controller) state.controller.abort();
  state.controller = new AbortController();
  state.loading = true;
  setLoading(reset);
  closeFilters();
  syncControlsFromState();
  updateDatePresentation();
  if (history) updateURL();

  const range = currentRange();
  const params = new URLSearchParams({
    from: range.from,
    to: range.to,
    limit: String(state.limit),
    offset: String(state.offset),
  });
  Object.entries(state.filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  params.set("sort", state.sort);

  try {
    const requests = [
      getJSON(`${API}/events?${params}`, state.controller.signal),
    ];
    if (reset) {
      requests.push(
        getJSON(
          `${API}/facets?from=${encodeURIComponent(range.from)}&to=${encodeURIComponent(range.to)}`,
          state.controller.signal,
        ),
      );
      if (state.view === "month") {
        requests.push(
          getJSON(`${API}/calendar?month=${state.anchor.slice(0, 7)}`, state.controller.signal),
        );
      }
    }

    const [events, facets, calendar] = await Promise.all(requests);
    if (reset) {
      state.items = events.items;
      state.facets = facets;
      populateFacets(facets);
      if (calendar) renderCalendar(calendar);
    } else {
      state.items.push(...events.items);
    }
    state.pagination = events.pagination;
    state.summary = events.summary;
    state.offset = state.items.length;
    renderResults();
    renderSummary();
    renderActiveFilters();
    dom.loadMoreButton.hidden = !events.pagination.has_more;
    dom.catalogStatus.textContent = state.meta?.updated_at
      ? `Updated ${relativeTime(state.meta.updated_at)}`
      : "Read-only catalog online";
  } catch (error) {
    if (error.name === "AbortError") return;
    renderError(error);
  } finally {
    state.loading = false;
    dom.results.setAttribute("aria-busy", "false");
    dom.loadMoreButton.disabled = false;
    dom.loadMoreButton.textContent = "Load more";
  }
}

function currentRange() {
  const anchor = parseISO(state.anchor);
  if (state.view === "day") {
    return { from: state.anchor, to: state.anchor };
  }
  if (state.view === "week") {
    const weekday = (anchor.getUTCDay() + 6) % 7;
    const from = addDays(anchor, -weekday);
    return { from: isoDate(from), to: isoDate(addDays(from, 6)) };
  }
  if (state.view === "month") {
    const from = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth(), 1));
    const to = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() + 1, 0));
    return { from: isoDate(from), to: isoDate(to) };
  }
  if (state.view === "upcoming") {
    const from = parseISO(today);
    return { from: today, to: isoDate(addDays(from, 29)) };
  }
  return { from: state.customFrom, to: state.customTo };
}

function shiftPeriod(direction) {
  const anchor = parseISO(state.anchor);
  if (state.view === "day") state.anchor = isoDate(addDays(anchor, direction));
  else if (state.view === "week") state.anchor = isoDate(addDays(anchor, direction * 7));
  else if (state.view === "month") {
    state.anchor = isoDate(
      new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() + direction, 1)),
    );
  } else if (state.view === "upcoming") {
    state.anchor = isoDate(addDays(anchor, direction * 30));
    state.view = "day";
  } else {
    const from = parseISO(state.customFrom);
    const to = parseISO(state.customTo);
    const width = Math.max(1, Math.round((to - from) / 86400000) + 1);
    state.customFrom = isoDate(addDays(from, direction * width));
    state.customTo = isoDate(addDays(to, direction * width));
    state.anchor = state.customFrom;
  }
  refresh({ reset: true });
}

function updateDatePresentation() {
  const range = currentRange();
  const from = parseISO(range.from);
  const to = parseISO(range.to);
  const same = range.from === range.to;

  if (same) {
    dom.dateHeading.textContent = formatDate(from, { weekday: "long" });
    dom.dateDisplay.textContent = formatDate(from, {
      month: "long",
      day: "numeric",
      year: "numeric",
    });
    dom.dateSubheading.textContent =
      range.from === today ? "Today · worldwide premieres" : "Worldwide premieres";
  } else if (state.view === "month") {
    dom.dateHeading.textContent = "Calendar month";
    dom.dateDisplay.textContent = formatDate(from, { month: "long", year: "numeric" });
    dom.dateSubheading.textContent = `${formatDate(from, { day: "numeric" })}–${formatDate(to, {
      day: "numeric",
      month: "long",
    })}`;
  } else {
    dom.dateHeading.textContent =
      state.view === "week" ? "Calendar week" : state.view === "upcoming" ? "Upcoming" : "Date range";
    dom.dateDisplay.textContent = `${formatDate(from, {
      month: "short",
      day: "numeric",
    })} — ${formatDate(to, {
      month: "short",
      day: "numeric",
      year: "numeric",
    })}`;
    dom.dateSubheading.textContent = `${daysBetween(from, to) + 1} days · worldwide premieres`;
  }

  dom.resultsKicker.textContent = state.view === "upcoming" ? "Upcoming series" : "Series premieres";
  dom.resultsTitle.textContent = same
    ? formatDate(from, { weekday: "long", month: "long", day: "numeric" })
    : `${formatDate(from, { month: "short", day: "numeric" })} to ${formatDate(to, {
        month: "short",
        day: "numeric",
      })}`;

  dom.calendarPanel.hidden = state.view !== "month";
  syncViewChips();
}

function renderResults() {
  dom.results.replaceChildren();
  const items = state.items;
  if (!items.length) {
    dom.results.append(
      element("div", { className: "empty-state" }, [
        element("strong", {}, "Nothing matched."),
        element(
          "p",
          {},
          "Try widening the date range or resetting filters. Zero-popularity titles are already included.",
        ),
      ]),
    );
    return;
  }

  const fragment = document.createDocumentFragment();
  items.forEach((item) => fragment.append(renderCard(item)));
  dom.results.append(fragment);
  if (window.htmx) window.htmx.process(dom.results);
}

function renderCard(item) {
  const card = element("article", { className: "premiere-card" });
  const posterButton = element("button", {
    className: "poster-button",
    type: "button",
    ariaLabel: `Open ${item.title.name}`,
  });
  prepareHTMXDetailButton(posterButton, item.title.id);
  if (item.title.poster_url) {
    const image = element("img", {
      src: item.title.poster_url,
      alt: "",
      loading: "lazy",
      decoding: "async",
      referrerPolicy: "no-referrer",
    });
    image.addEventListener("error", () => {
      posterButton.replaceChildren(posterPlaceholder(item.title.name));
    }, { once: true });
    posterButton.append(image);
  } else {
    posterButton.append(posterPlaceholder(item.title.name));
  }

  const titleButton = element("button", {
    className: "card-title-button",
    type: "button",
  }, element("h2", {}, item.title.name));
  prepareHTMXDetailButton(titleButton, item.title.id);

  const body = element("div", { className: "premiere-card__body" }, [titleButton]);
  if (item.title.original_name && item.title.original_name !== item.title.name) {
    body.append(element("p", { className: "original-title" }, item.title.original_name));
  }

  const metadata = element("div", { className: "metadata-line" });
  const countryText = item.countries.map(countryName).join(", ");
  if (countryText) metadata.append(element("span", {}, countryText));
  if (item.title.language) metadata.append(element("span", {}, languageName(item.title.language)));
  if (item.title.format) metadata.append(element("span", {}, item.title.format));
  if (item.title.runtime_minutes) {
    metadata.append(element("span", {}, `${item.title.runtime_minutes} min`));
  }
  const networks = item.networks.map((network) => network.name).slice(0, 2).join(", ");
  if (networks) metadata.append(element("span", {}, networks));
  body.append(metadata);

  if (item.title.overview) {
    body.append(element("p", { className: "overview" }, item.title.overview));
  }

  const tags = element("div", { className: "tag-row" });
  tags.append(element("span", { className: "tag" }, eventTypeLabel(item.event_type)));
  item.genres.slice(0, 3).forEach((genre) => tags.append(element("span", { className: "tag" }, genre)));
  const sources = [...new Set(item.evidence.map((evidence) => evidence.source))];
  sources.forEach((source) => tags.append(element("span", { className: "source-badge" }, sourceLabel(source))));
  tags.append(
    element(
      "span",
      { className: `confidence-badge${item.date_conflict ? " is-conflict" : ""}` },
      dateAssessmentLabel(item),
    ),
  );
  body.append(tags);

  const eventDate = parseISO(item.date);
  const dateBlock = element("div", { className: "premiere-card__date" }, [
    element("strong", {}, String(eventDate.getUTCDate()).padStart(2, "0")),
    element("span", {}, formatDate(eventDate, { month: "short" })),
  ]);
  const dots = element("div", { className: "source-dots", ariaLabel: "Metadata sources" });
  sources.forEach((source) => dots.append(element("span", {
    className: "source-dot",
    dataset: { source },
    title: sourceLabel(source),
  })));
  dateBlock.append(dots);

  card.append(posterButton, body, dateBlock);
  return card;
}

function prepareHTMXDetailButton(button, titleId) {
  button.setAttribute("hx-get", `/ui/v1/titles/${encodeURIComponent(titleId)}`);
  button.setAttribute("hx-target", "#detailContent");
  button.setAttribute("hx-swap", "innerHTML transition:true");
  button.addEventListener("click", () => {
    dom.detailContent.replaceChildren(
      element("div", { className: "loading-state htmx-indicator" }, [
        element("div", { className: "loader", ariaHidden: "true" }),
        element("p", {}, "Resolving source evidence…"),
      ]),
    );
    dom.detailDialog.showModal();
    if (!window.htmx) openDetail(titleId);
  });
}

async function openDetail(titleId) {
  dom.detailContent.replaceChildren(
    element("div", { className: "loading-state" }, [
      element("div", { className: "loader", ariaHidden: "true" }),
      element("p", {}, "Resolving source evidence…"),
    ]),
  );
  dom.detailDialog.showModal();

  try {
    const item = await getJSON(`${API}/titles/${encodeURIComponent(titleId)}`);
    renderDetail(item);
  } catch (error) {
    dom.detailContent.replaceChildren(
      element("div", { className: "error-state" }, [
        element("strong", {}, "Could not open this title."),
        element("p", {}, error.message),
      ]),
    );
  }
}

function renderDetail(item) {
  const hero = element("section", { className: "detail-hero" });
  if (item.title.backdrop_url) {
    const backdrop = element("img", {
      className: "detail-hero__backdrop",
      src: item.title.backdrop_url,
      alt: "",
      referrerPolicy: "no-referrer",
    });
    backdrop.addEventListener("error", () => backdrop.remove(), { once: true });
    hero.append(backdrop);
  }

  const poster = element("div", { className: "detail-poster" });
  if (item.title.poster_url) {
    const image = element("img", {
      src: item.title.poster_url,
      alt: "",
      referrerPolicy: "no-referrer",
    });
    image.addEventListener("error", () => {
      poster.replaceChildren(posterPlaceholder(item.title.name));
    }, { once: true });
    poster.append(image);
  } else {
    poster.append(posterPlaceholder(item.title.name));
  }

  const title = element("div", { className: "detail-title" }, [
    element("span", { className: "section-kicker" }, eventTypeLabel(item.event_type)),
    element("h2", {}, item.title.name),
  ]);
  if (item.title.original_name && item.title.original_name !== item.title.name) {
    title.append(element("p", {}, item.title.original_name));
  }
  hero.append(element("div", { className: "detail-hero__content" }, [poster, title]));

  const left = element("div");
  left.append(
    element(
      "p",
      { className: "detail-overview" },
      item.title.overview || "No overview has been supplied by the current metadata sources.",
    ),
  );

  const evidenceSection = element("section", { className: "detail-section" }, [
    element("h3", {}, "Date assessment"),
  ]);
  const assessment = item.date_assessment;
  if (assessment) {
    evidenceSection.append(
      element("div", { className: "date-assessment" }, [
        element("strong", {}, `${assessment.meaning_label} · ${assessment.status.replaceAll("_", " ")}`),
        element("p", {}, assessment.meaning_description),
        element("small", {}, assessment.explanation),
      ]),
    );
  }
  if (item.date_conflict) {
    evidenceSection.append(
      element(
        "p",
        { className: "quality-warning" },
        "Sources report different dates. Every reported date is retained below.",
      ),
    );
  }
  const evidenceList = element("div", { className: "evidence-list" });
  item.evidence.forEach((evidence) => {
    const relation = evidence.supports_selected_date
      ? "Selected date"
      : evidence.difference_days == null
        ? "Other provider date"
        : `${evidence.difference_days > 0 ? "+" : ""}${evidence.difference_days} day${Math.abs(evidence.difference_days) === 1 ? "" : "s"}`;
    const row = element("div", { className: "evidence-item" }, [
      element("strong", {}, sourceLabel(evidence.source)),
      element("span", {}, `${formatDate(parseISO(evidence.reported_date), {
        month: "long", day: "numeric", year: "numeric",
      })} · ${relation}`),
    ]);
    if (evidence.url) {
      row.append(element("a", {
        href: evidence.url,
        target: "_blank",
        rel: "noreferrer",
      }, "Open source"));
    } else {
      row.append(element("span"));
    }
    evidenceList.append(row);
  });
  evidenceSection.append(evidenceList);
  if (!item.evidence.length) {
    evidenceList.append(element("p", {}, "No event evidence is currently available."));
  }
  left.append(evidenceSection);

  if (item.aliases?.length) {
    const aliasNames = [...new Set(item.aliases.map((alias) => alias.name))];
    const aliasSection = element("section", { className: "detail-section" }, [
      element("h3", {}, "Known titles and aliases"),
      element("p", {}, aliasNames.join(" · ")),
    ]);
    left.append(aliasSection);
  }

  const linkSection = element("section", { className: "detail-section" }, [
    element("h3", {}, "External records"),
  ]);
  const links = element("div", { className: "external-links" });
  item.external_ids
    .filter((external) => external.url)
    .forEach((external) => {
      links.append(element("a", {
        href: external.url,
        target: "_blank",
        rel: "noreferrer",
      }, `${sourceLabel(external.source)} · ${external.id}`));
    });
  linkSection.append(links);
  left.append(linkSection);

  if (item.quality_flags.length) {
    const important = item.quality_flags.filter((flag) =>
      ["provider_problematic_entry", "identity_conflict", "identity_key_collision"].includes(flag.flag),
    );
    if (important.length) {
      left.append(
        element(
          "div",
          { className: "quality-warning" },
          `Metadata warning: ${important.map((flag) => flag.detail || flag.flag).join(" ")}`,
        ),
      );
    }
  }

  const facts = element("aside", { className: "detail-facts" });
  addFact(
    facts,
    "Canonical premiere",
    item.date
      ? formatDate(parseISO(item.date), { month: "long", day: "numeric", year: "numeric" })
      : "Not currently known",
  );
  addFact(
    facts,
    "Date status",
    item.date_assessment
      ? item.date_assessment.status.replaceAll("_", " ")
      : item.date_conflict ? "Sources disagree" : item.date ? "Sources aligned" : "No date",
  );
  if (item.date_assessment) addFact(facts, "Date meaning", item.date_assessment.meaning_label);
  addFact(facts, "Origin", item.countries.map(countryName).join(", ") || "Unknown");
  addFact(facts, "Language", item.title.language ? languageName(item.title.language) : "Unknown");
  addFact(facts, "Format", item.title.format || "Unknown");
  addFact(facts, "Network / service", item.networks.map((value) => value.name).join(", ") || "Unknown");
  addFact(facts, "Genres", item.genres.join(", ") || "Unclassified");
  addFact(facts, "Confidence", `${Math.round(item.confidence * 100)}%`);
  if (item.title.runtime_minutes) addFact(facts, "Typical runtime", `${item.title.runtime_minutes} minutes`);

  if (item.events?.length > 1) {
    addFact(facts, "Recorded events", String(item.events.length));
  }

  const body = element("section", { className: "detail-body" }, [left, facts]);
  dom.detailContent.replaceChildren(hero, body);
}

function addFact(container, label, value) {
  container.append(
    element("div", { className: "detail-fact" }, [
      element("span", {}, label),
      element("strong", {}, value),
    ]),
  );
}

function renderSummary() {
  const total = state.pagination?.total ?? state.items.length;
  dom.resultCount.textContent = number(total);
  dom.countryCount.textContent = number(state.facets?.countries?.length || 0);
  dom.sourceCount.textContent = number(state.facets?.sources?.length || 0);
  const conflicts = state.summary?.date_conflicts ?? 0;
  dom.conflictCount.textContent = number(conflicts);
}

function renderCalendar(data) {
  const counts = new Map(data.days.map((day) => [day.date, day]));
  const anchor = parseISO(`${data.month}-01`);
  const year = anchor.getUTCFullYear();
  const month = anchor.getUTCMonth();
  const firstWeekday = (anchor.getUTCDay() + 6) % 7;
  const gridStart = addDays(anchor, -firstWeekday);

  dom.calendarGrid.replaceChildren();
  const fragment = document.createDocumentFragment();
  for (let index = 0; index < 42; index += 1) {
    const current = addDays(gridStart, index);
    const value = isoDate(current);
    const record = counts.get(value);
    const button = element("button", {
      className: `calendar-day${current.getUTCMonth() !== month ? " is-outside" : ""}${
        value === today ? " is-today" : ""
      }`,
      type: "button",
      ariaLabel: `${formatDate(current, {
        month: "long",
        day: "numeric",
        year: "numeric",
      })}: ${record?.count || 0} premieres`,
    });
    button.append(element("span", { className: "calendar-day__number" }, String(current.getUTCDate())));
    if (record?.conflicts) {
      button.append(element("span", {
        className: "calendar-day__conflict",
        title: `${record.conflicts} date disagreement${record.conflicts === 1 ? "" : "s"}`,
      }));
    }
    button.append(
      element("span", { className: "calendar-day__count" }, [
        element("strong", {}, String(record?.count || 0)),
        element("span", {}, record?.count === 1 ? "premiere" : "premieres"),
      ]),
    );
    button.addEventListener("click", () => {
      state.anchor = value;
      state.view = "day";
      refresh({ reset: true });
    });
    fragment.append(button);
  }
  dom.calendarGrid.append(fragment);
}

function renderActiveFilters() {
  dom.activeFilters.replaceChildren();
  const labels = {
    q: "Search",
    country: "Country",
    language: "Language",
    network: "Network",
    genre: "Genre",
    format: "Format",
    source: "Source",
    event_type: "Event type",
    confidence: "Confidence",
    conflict: "Dates",
  };

  Object.entries(state.filters).forEach(([key, value]) => {
    if (!value) return;
    let shown = value;
    if (key === "country") shown = countryName(value);
    if (key === "language") shown = languageName(value);
    if (key === "source") shown = sourceLabel(value);
    if (key === "event_type") shown = eventTypeLabel(value);
    if (key === "confidence") shown = `${value[0].toUpperCase()}${value.slice(1)}`;
    if (key === "conflict") shown = value === "only" ? "Disagreements only" : "Agreed only";

    const chip = element("span", { className: "active-filter" }, [
      element("span", {}, `${labels[key]}: ${shown}`),
    ]);
    const remove = element("button", {
      type: "button",
      ariaLabel: `Remove ${labels[key]} filter`,
    }, "×");
    remove.addEventListener("click", () => {
      state.filters[key] = "";
      syncControlsFromState();
      refresh({ reset: true });
    });
    chip.append(remove);
    dom.activeFilters.append(chip);
  });
}

function populateFacets(facets) {
  populateCountryPicker(facets.countries);
  populateSelect(dom.languageFilter, facets.languages, "All languages", (value) => languageName(value));
  populateSelect(dom.networkFilter, facets.networks, "All networks");
  populateSelect(dom.genreFilter, facets.genres, "All genres");
  populateSelect(dom.formatFilter, facets.formats, "All formats");
  populateSelect(dom.eventTypeFilter, facets.event_types, "All event types", eventTypeLabel);
}

function populateCountryPicker(values) {
  dom.countryFilterOptions.replaceChildren();
  const choices = [{ value: "", count: null }, ...values];
  choices.forEach((item) => {
    const selected = item.value === state.filters.country;
    const label = item.value
      ? `${countryName(item.value)} (${number(item.count)})`
      : "All countries";
    const button = element("button", {
      className: selected
        ? "block w-full rounded-lg bg-orange-600 px-3 py-2 text-left text-sm font-bold text-white"
        : "block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-black/5 dark:hover:bg-white/10",
      type: "button",
      role: "option",
      ariaSelected: String(selected),
    }, label);
    button.addEventListener("click", () => {
      if (state.filters.country === item.value) {
        dom.countryPicker.removeAttribute("open");
        return;
      }
      state.filters.country = item.value;
      dom.countryPicker.removeAttribute("open");
      syncCountryPicker();
      refresh({ reset: true });
    });
    dom.countryFilterOptions.append(button);
  });
  syncCountryPicker();
}

function syncCountryPicker() {
  const selected = state.filters.country;
  const match = state.facets?.countries?.find((item) => item.value === selected);
  dom.countryFilterSummary.textContent = selected
    ? `${countryName(selected)}${match ? ` (${number(match.count)})` : ""}`
    : "All countries";
  [...dom.countryFilterOptions.children].forEach((button) => {
    const active = button.textContent === dom.countryFilterSummary.textContent;
    button.setAttribute("aria-selected", String(active));
  });
}

function populateSelect(select, values, allLabel, labeler = (value) => value) {
  const key = filterKeyFor(select);
  const selected = state.filters[key] || "";
  select.replaceChildren(element("option", { value: "" }, allLabel));
  values.forEach((item) => {
    select.append(element("option", { value: item.value }, `${labeler(item.value)} (${number(item.count)})`));
  });
  const available = !selected || [...select.options].some((option) => option.value === selected);
  if (selected && !available) {
    select.append(element("option", { value: selected }, `${labeler(selected)} (0)`));
  }
  select.value = selected;
}

function filterKeyFor(select) {
  return {
    languageFilter: "language",
    networkFilter: "network",
    genreFilter: "genre",
    formatFilter: "format",
    eventTypeFilter: "event_type",
  }[select.id];
}

function syncControlsFromState() {
  dom.searchInput.value = state.filters.q;
  syncCountryPicker();
  dom.languageFilter.value = state.filters.language;
  dom.networkFilter.value = state.filters.network;
  dom.genreFilter.value = state.filters.genre;
  dom.formatFilter.value = state.filters.format;
  dom.sourceFilter.value = state.filters.source;
  dom.eventTypeFilter.value = state.filters.event_type;
  dom.confidenceFilter.value = state.filters.confidence;
  dom.sortSelect.value = state.sort;
  dom.conflictInputs.forEach((input) => {
    input.checked = input.value === state.filters.conflict;
  });
  dom.customFrom.value = state.customFrom;
  dom.customTo.value = state.customTo;
  dom.customRangeForm.hidden = state.view !== "custom";
  syncViewChips();
}

function syncViewChips() {
  dom.rangeChips.forEach((button) => {
    let active = button.dataset.view === state.view;
    if (button.dataset.relative === "today") active = active && state.anchor === today;
    if (button.dataset.relative === "tomorrow") active = active && state.anchor === tomorrow;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function applyMeta(meta) {
  const name = meta.site_name || "Global Media Discovery";
  document.title = name;
  dom.brandName.textContent = name;
  dom.footerName.textContent = name;
  dom.catalogStatus.textContent = meta.updated_at
    ? `Updated ${relativeTime(meta.updated_at)}`
    : `${number(meta.title_count || 0)} titles indexed`;
}

function renderCredits(credits) {
  dom.creditsSources.replaceChildren();
  credits.sources.forEach((source) => {
    dom.creditsSources.append(
      element("div", { className: "credit-source" }, [
        element("a", {
          href: source.url,
          target: "_blank",
          rel: "noreferrer",
        }, source.name),
        element("p", {}, source.notice),
      ]),
    );
  });
}

function clearFilters() {
  Object.keys(state.filters).forEach((key) => {
    state.filters[key] = "";
  });
  syncControlsFromState();
  refresh({ reset: true });
}

function setLoading(reset) {
  dom.results.setAttribute("aria-busy", "true");
  if (reset) {
    dom.results.replaceChildren(
      element("div", { className: "loading-state" }, [
        element("div", { className: "loader", ariaHidden: "true" }),
        element("p", {}, "Reading the worldwide calendar…"),
      ]),
    );
  }
  dom.loadMoreButton.disabled = true;
  dom.loadMoreButton.textContent = reset ? "Load more" : "Loading…";
}

function renderError(error) {
  const offline = !navigator.onLine;
  dom.results.replaceChildren(
    element("div", { className: "error-state" }, [
      element("strong", {}, offline ? "You appear to be offline." : "The catalog could not be loaded."),
      element(
        "p",
        {},
        offline ? "Reconnect to load current catalog data." : error.message || "Please try again.",
      ),
      (() => {
        const button = element("button", { className: "secondary-button", type: "button" }, "Try again");
        button.addEventListener("click", () => refresh({ reset: true }));
        return button;
      })(),
    ]),
  );
  dom.loadMoreButton.hidden = true;
  dom.catalogStatus.textContent = offline ? "Offline" : "Catalog temporarily unavailable";
}

function updateURL() {
  const params = new URLSearchParams();
  params.set("view", state.view);
  params.set("date", state.anchor);
  if (state.view === "custom") {
    params.set("from", state.customFrom);
    params.set("to", state.customTo);
  }
  Object.entries(state.filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  if (state.sort !== "date_asc") params.set("sort", state.sort);
  const next = `${location.pathname}?${params.toString()}`;
  history.replaceState(null, "", next);
}

function openFilters() {
  dom.filtersPanel.classList.add("is-open");
  dom.filterScrim.hidden = false;
  document.body.classList.add("filters-open");
  dom.mobileFilterButton.setAttribute("aria-expanded", "true");
  dom.closeFiltersButton.focus();
}

function closeFilters() {
  dom.filtersPanel.classList.remove("is-open");
  dom.filterScrim.hidden = true;
  document.body.classList.remove("filters-open");
  dom.mobileFilterButton.setAttribute("aria-expanded", "false");
}

function restoreTheme() {
  const theme = localStorage.getItem("gmd-theme") || "system";
  document.documentElement.dataset.theme = theme;
  dom.themeButton.dataset.theme = theme;
  updateThemeLabel(theme);
}

function cycleTheme() {
  const order = ["system", "light", "dark"];
  const current = document.documentElement.dataset.theme || "system";
  const next = order[(order.indexOf(current) + 1) % order.length];
  document.documentElement.dataset.theme = next;
  dom.themeButton.dataset.theme = next;
  localStorage.setItem("gmd-theme", next);
  updateThemeLabel(next);
  showToast(`Theme: ${next}`);
}

function updateThemeLabel(theme) {
  const label = `Color theme: ${theme}. Activate to change theme.`;
  dom.themeButton.setAttribute("aria-label", label);
  dom.themeButton.title = label;
}

function posterPlaceholder(title) {
  return element("span", { className: "poster-placeholder" }, title.slice(0, 40));
}

function confidenceLabel(value) {
  if (value >= 0.9) return "High confidence";
  if (value >= 0.7) return "Good confidence";
  return "Single-source";
}

function dateAssessmentLabel(item) {
  const assessment = item.date_assessment;
  if (!assessment) return item.date_conflict ? "Date disagreement" : confidenceLabel(item.confidence);
  if (assessment.status === "disputed") {
    return `${assessment.distinct_date_count} dates reported`;
  }
  if (assessment.status === "corroborated") {
    return `${assessment.source_count} sources agree`;
  }
  if (assessment.status === "single_source") return "Single-source date";
  return "Date unverified";
}

function sourceLabel(source) {
  return {
    tmdb: "TMDB",
    tvdb: "TheTVDB",
    tvmaze: "TVmaze",
    imdb: "IMDb",
    wikidata: "Wikidata",
    wikipedia: "Wikipedia",
  }[source] || source;
}

function eventTypeLabel(value) {
  return {
    series_premiere: "Series premiere",
    season_premiere: "Season premiere",
    special: "Special",
  }[value] || (value ? value.replaceAll("_", " ") : "Undated title");
}

function countryName(code) {
  try {
    return new Intl.DisplayNames([displayLocale], { type: "region" }).of(code) || code;
  } catch {
    return code;
  }
}

function languageName(code) {
  try {
    return new Intl.DisplayNames([displayLocale], { type: "language" }).of(code) || code;
  } catch {
    return code;
  }
}

async function getJSON(url, signal) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const requestURL = new URL(url, location.origin);
    if (attempt) requestURL.searchParams.set("_fresh", String(Date.now()));
    const response = await fetch(requestURL, {
      cache: attempt ? "reload" : "no-store",
      headers: { Accept: "application/json" },
      signal,
    });
    const body = await response.text();
    if ((response.status === 304 || (response.ok && !body.trim())) && attempt === 0) {
      continue;
    }

    let data = null;
    if (body.trim()) {
      try {
        data = JSON.parse(body);
      } catch {
        throw new Error("The catalog returned invalid data. Please try again.");
      }
    }

    if (!response.ok) {
      const message = data?.error?.message || `Request failed with status ${response.status}.`;
      throw new Error(message);
    }
    if (data === null) {
      throw new Error("The catalog returned an empty response. Please try again.");
    }
    return data;
  }

  throw new Error("The catalog returned an empty response. Please try again.");
}

function element(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(attributes).forEach(([key, value]) => {
    if (value === undefined || value === null || value === false) return;
    if (key === "className") node.className = value;
    else if (key === "ariaLabel") node.setAttribute("aria-label", value);
    else if (key === "ariaHidden") node.setAttribute("aria-hidden", value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  });
  const list = Array.isArray(children) ? children : [children];
  list.forEach((child) => {
    if (child === undefined || child === null || child === false) return;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  });
  return node;
}

function showToast(message) {
  dom.toast.textContent = message;
  dom.toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    dom.toast.hidden = true;
  }, 2200);
}

function parseISO(value) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

function isoDate(value) {
  return value.toISOString().slice(0, 10);
}

function localISODate(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function addDays(value, days) {
  const copy = new Date(value.getTime());
  copy.setUTCDate(copy.getUTCDate() + days);
  return copy;
}

function daysBetween(from, to) {
  return Math.round((to - from) / 86400000);
}

function formatDate(value, options) {
  return new Intl.DateTimeFormat(displayLocale, {
    timeZone: "UTC",
    ...options,
  }).format(value);
}

function number(value) {
  return new Intl.NumberFormat(displayLocale).format(value);
}

function validDate(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const parsed = parseISO(value);
  return !Number.isNaN(parsed.getTime()) && isoDate(parsed) === value ? value : null;
}

function validView(value) {
  return ["day", "week", "month", "upcoming", "custom"].includes(value) ? value : null;
}

function validSort(value) {
  return ["date_asc", "date_desc", "title_asc", "confidence_desc"].includes(value)
    ? value
    : null;
}

function safeLocale(value) {
  try {
    return Intl.getCanonicalLocales(value)[0] || "en-US";
  } catch {
    return "en-US";
  }
}

function relativeTime(value) {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return value;
  const seconds = Math.round((timestamp - Date.now()) / 1000);
  const absolute = Math.abs(seconds);
  const formatter = new Intl.RelativeTimeFormat(displayLocale, { numeric: "auto" });
  if (absolute < 90) return formatter.format(seconds, "second");
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 90) return formatter.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 36) return formatter.format(hours, "hour");
  return formatter.format(Math.round(hours / 24), "day");
}
