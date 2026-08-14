"""Read-only HTML fragments for the HTMX-enhanced public interface."""

from __future__ import annotations

from html import escape
import re
from typing import Any, Callable
from urllib.parse import urlsplit
import uuid

from gmd.config import Settings
from gmd.query import CatalogQueries


_TITLE_ID_RE = re.compile(r"^[a-z0-9_]{8,80}$")
_PANEL = (
    "rounded-2xl border border-black/10 bg-white/80 p-5 shadow-sm "
    "dark:border-white/10 dark:bg-zinc-900/85"
)
_PILL = (
    "inline-flex rounded-full border border-black/10 bg-black/5 px-2.5 py-1 "
    "text-xs font-semibold dark:border-white/10 dark:bg-white/10"
)


class ReadOnlyUI:
    """Serve escaped HTML fragments without adding public write capability."""

    def __init__(self, settings: Settings) -> None:
        self.queries = CatalogQueries(settings.database_path)

    def __call__(
        self,
        environ: dict[str, Any],
        start_response: Callable[..., Any],
    ) -> list[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/"))
        request_id = uuid.uuid4().hex[:16]

        if method not in {"GET", "HEAD"}:
            return self._respond(
                start_response,
                405,
                self._error("This public interface is read-only."),
                method,
                request_id,
                [("Allow", "GET, HEAD")],
            )

        prefix = "/ui/v1/titles/"
        if path.startswith(prefix):
            title_id = path[len(prefix):]
            if not _TITLE_ID_RE.fullmatch(title_id):
                status = 400
                content = self._error("Invalid title identifier.")
            else:
                item = self.queries.title(title_id)
                status = 200 if item else 404
                content = render_title(item) if item else self._error("Title not found.")
        elif path == "/ui/v1/credits":
            status = 200
            content = render_credits(self.queries.credits())
        elif path == "/ui/v1/coverage":
            status = 200
            content = render_coverage(self.queries.coverage())
        else:
            status = 404
            content = self._error("Interface route not found.")

        return self._respond(start_response, status, content, method, request_id)

    @staticmethod
    def _error(message: str) -> str:
        return (
            f'<div class="{_PANEL}" role="alert">'
            '<strong class="block text-sm font-bold">Unable to load content</strong>'
            f'<p class="mt-2 text-sm text-zinc-600 dark:text-zinc-300">{_e(message)}</p>'
            "</div>"
        )

    @staticmethod
    def _respond(
        start_response: Callable[..., Any],
        status: int,
        content: str,
        method: str,
        request_id: str,
        extra: list[tuple[str, str]] | None = None,
    ) -> list[bytes]:
        body = content.encode("utf-8")
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Request-ID", request_id),
        ]
        headers.extend(extra or [])
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}
        start_response(f"{status} {reason[status]}", headers)
        return [] if method == "HEAD" else [body]


def render_title(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    title = item["title"]
    name = _e(title.get("name") or "Untitled")
    event_type = _label(item.get("event_type") or "premiere")
    overview = _e(
        title.get("overview")
        or "No overview has been supplied by the current metadata sources."
    )
    poster = _safe_url(title.get("poster_url"))
    poster_html = (
        f'<img class="h-full w-full object-cover" src="{_e(poster)}" alt="" '
        'referrerpolicy="no-referrer">'
        if poster
        else (
            '<div class="grid h-full w-full place-items-center bg-zinc-200 text-3xl '
            'font-black text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">'
            f"{_e(_initials(title.get('name') or ''))}</div>"
        )
    )
    metadata = [
        *(item.get("countries") or []),
        title.get("language"),
        title.get("format"),
        f"{title['runtime_minutes']} min" if title.get("runtime_minutes") else None,
    ]
    metadata_html = "".join(
        f'<span class="{_PILL}">{_e(value)}</span>' for value in metadata if value
    )
    genres_html = "".join(
        f'<span class="{_PILL}">{_e(genre)}</span>' for genre in item.get("genres", [])
    )
    networks_html = "".join(
        f'<li class="text-sm">{_e(network.get("name"))}</li>'
        for network in item.get("networks", [])
    ) or '<li class="text-sm text-zinc-500">No network supplied</li>'
    evidence_html = "".join(_render_evidence(row) for row in item.get("evidence", []))
    assessment = item.get("date_assessment") or {}
    assessment_status = _label(assessment.get("status") or "unverified")
    assessment_tone = (
        "border-amber-500/30 bg-amber-500/10 text-amber-950 dark:text-amber-100"
        if assessment.get("status") == "disputed"
        else "border-emerald-600/20 bg-emerald-600/10 text-emerald-950 dark:text-emerald-100"
    )
    assessment_html = (
        f'<div class="mt-3 rounded-xl border p-4 {assessment_tone}">'
        f'<div class="flex flex-wrap items-center justify-between gap-2">'
        f'<strong class="text-sm">{_e(assessment.get("meaning_label") or event_type)}</strong>'
        f'<span class="rounded-full bg-black/10 px-2 py-1 text-xs font-black uppercase '
        f'tracking-wide dark:bg-white/10">{_e(assessment_status)}</span></div>'
        f'<p class="mt-2 text-sm leading-6">{_e(assessment.get("meaning_description"))}</p>'
        f'<p class="mt-2 text-xs leading-5 opacity-80">{_e(assessment.get("explanation"))}</p>'
        '</div>'
    )
    aliases_html = "".join(
        f'<li class="text-sm">{_e(alias.get("name"))}</li>'
        for alias in item.get("aliases", [])
    ) or '<li class="text-sm text-zinc-500">No alternate titles supplied</li>'
    conflict = (
        '<p class="mt-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 '
        'text-sm font-semibold text-amber-900 dark:text-amber-200">'
        "Sources report different dates. Every reported date is retained below.</p>"
        if item.get("date_conflict")
        else ""
    )
    return f"""
<article class="text-gmd-ink dark:text-zinc-100" data-htmx-fragment="title-detail">
  <div class="grid gap-6 md:grid-cols-[11rem_1fr]">
    <div class="aspect-[2/3] overflow-hidden rounded-2xl border border-black/10
                dark:border-white/10">{poster_html}</div>
    <div class="self-end">
      <p class="text-gmd-accent text-xs font-black tracking-[0.18em] uppercase">{event_type}</p>
      <h2 class="mt-2 text-3xl font-black tracking-tight md:text-5xl">{name}</h2>
      <div class="mt-4 flex flex-wrap gap-2">{metadata_html}</div>
      <div class="mt-2 flex flex-wrap gap-2">{genres_html}</div>
    </div>
  </div>
  <div class="mt-7 grid gap-5 lg:grid-cols-[1.4fr_1fr]">
    <section class="{_PANEL}">
      <h3 class="text-sm font-black tracking-wide uppercase">Overview</h3>
      <p class="mt-3 text-sm leading-7 text-zinc-700 dark:text-zinc-300">{overview}</p>
    </section>
    <section class="{_PANEL}">
      <h3 class="text-sm font-black tracking-wide uppercase">Networks / services</h3>
      <ul class="mt-3 grid gap-2">{networks_html}</ul>
    </section>
    <section class="{_PANEL}">
      <h3 class="text-sm font-black tracking-wide uppercase">Date assessment</h3>
      {assessment_html}
      {conflict}
      <h4 class="mt-5 text-xs font-black tracking-wide uppercase">Provider reports</h4>
      <div class="mt-3 grid gap-2">{evidence_html}</div>
    </section>
    <section class="{_PANEL}">
      <h3 class="text-sm font-black tracking-wide uppercase">Alternate titles</h3>
      <ul class="mt-3 grid gap-2">{aliases_html}</ul>
    </section>
  </div>
</article>
""".strip()


def render_credits(payload: dict[str, Any]) -> str:
    cards = []
    for source in payload.get("sources", []):
        name = _e(source.get("name"))
        notice = _e(source.get("notice"))
        url = _safe_url(source.get("url"))
        heading = (
            f'<a class="font-black underline decoration-gmd-accent decoration-2 '
            f'underline-offset-4" href="{_e(url)}" rel="noreferrer" target="_blank">'
            f"{name}</a>"
            if url
            else f'<strong class="font-black">{name}</strong>'
        )
        cards.append(
            f'<article class="{_PANEL}">{heading}'
            f'<p class="mt-2 text-sm text-zinc-600 dark:text-zinc-300">{notice}</p></article>'
        )
    return '<div class="grid gap-3 sm:grid-cols-3">' + "".join(cards) + "</div>"


def render_coverage(payload: dict[str, Any]) -> str:
    bounds = payload.get("date_bounds") or {}
    summary = (
        f'{int(payload.get("title_count") or 0):,} titles · '
        f'{int(payload.get("event_count") or 0):,} events · '
        f'{int(payload.get("evidence_count") or 0):,} provider reports'
    )
    source_cards = []
    for source in payload.get("sources", []):
        source_cards.append(
            f'<article class="{_PANEL}">'
            f'<strong class="text-sm font-black">{_e(_label(source.get("source")))}</strong>'
            f'<p class="mt-2 text-2xl font-black">{int(source.get("evidence_count") or 0):,}</p>'
            f'<p class="text-xs text-zinc-600 dark:text-zinc-300">reports · '
            f'{_e(source.get("reported_date_min"))} to {_e(source.get("reported_date_max"))}</p>'
            '</article>'
        )
    year_rows = []
    for row in payload.get("years", []):
        year_rows.append(
            '<tr class="border-t border-black/10 dark:border-white/10">'
            f'<th class="py-2 pr-4 text-left font-black">{int(row.get("year") or 0)}</th>'
            f'<td class="px-3 py-2 text-right">{int(row.get("title_count") or 0):,}</td>'
            f'<td class="px-3 py-2 text-right">{int(row.get("event_count") or 0):,}</td>'
            f'<td class="px-3 py-2 text-right">{int(row.get("active_day_count") or 0):,}</td>'
            f'<td class="pl-3 py-2 text-right">{int(row.get("conflict_count") or 0):,}</td>'
            '</tr>'
        )
    return f"""
<div data-htmx-fragment="coverage">
  <p class="text-lg font-black">{_e(summary)}</p>
  <p class="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-300">
    {_e(payload.get("scope"))}
  </p>
  <p class="mt-2 text-xs font-semibold text-zinc-500">Observed date range:
     {_e(bounds.get("min"))} to {_e(bounds.get("max"))} ·
     {int(payload.get("active_day_count") or 0):,} active dates</p>
  <div class="mt-5 grid gap-3 sm:grid-cols-3">{"".join(source_cards)}</div>
  <div class="mt-5 overflow-x-auto">
    <table class="w-full text-sm">
      <caption class="pb-2 text-left text-xs font-black tracking-wide uppercase">
        Coverage by year
      </caption>
      <thead><tr class="text-xs text-zinc-500">
        <th class="pb-2 pr-4 text-left">Year</th><th class="px-3 pb-2 text-right">Titles</th>
        <th class="px-3 pb-2 text-right">Events</th>
        <th class="px-3 pb-2 text-right">Active days</th>
        <th class="pb-2 pl-3 text-right">Conflicts</th>
      </tr></thead><tbody>{"".join(year_rows)}</tbody>
    </table>
  </div>
</div>
""".strip()


def _render_evidence(row: dict[str, Any]) -> str:
    source = _label(row.get("source") or "source")
    reported = _e(row.get("reported_date") or "Unknown date")
    url = _safe_url(row.get("url"))
    content = (
        f'<a class="font-bold underline decoration-gmd-accent underline-offset-4" '
        f'href="{_e(url)}" rel="noreferrer" target="_blank">{_e(source)}</a>'
        if url
        else f'<strong>{_e(source)}</strong>'
    )
    supports = bool(row.get("supports_selected_date"))
    delta = row.get("difference_days")
    if supports:
        relation = "Selected date"
    elif isinstance(delta, int):
        relation = f'{delta:+d} day{"s" if abs(delta) != 1 else ""} from selected'
    else:
        relation = "Other provider date"
    confidence = round(float(row.get("confidence") or 0) * 100)
    observed = str(row.get("observed_at") or "")[:10]
    marker = (
        "bg-emerald-600/10 text-emerald-800 dark:text-emerald-200"
        if supports
        else "bg-amber-500/10 text-amber-900 dark:text-amber-200"
    )
    return (
        '<div class="grid gap-2 rounded-xl border border-black/10 bg-black/5 p-3 '
        'dark:border-white/10 dark:bg-white/5 sm:grid-cols-[minmax(5rem,0.6fr)_1fr_auto] '
        'sm:items-center">'
        f'<div>{content}<p class="mt-1 text-xs opacity-60">'
        f'{confidence}% provider confidence</p></div>'
        f'<div><time class="text-sm font-bold">{reported}</time>'
        f'<p class="mt-1 text-xs opacity-60">Observed {_e(observed or "unknown")}</p></div>'
        f'<span class="w-fit rounded-full px-2 py-1 text-xs font-bold {marker}">'
        f'{_e(relation)}</span>'
        '</div>'
    )


def _safe_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlsplit(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _initials(value: str) -> str:
    words = [word for word in value.split() if word]
    return "".join(word[0] for word in words[:2]).upper() or "TV"


def _label(value: object) -> str:
    return str(value).replace("_", " ").strip().title()


def _e(value: object) -> str:
    return escape(str(value or ""), quote=True)
