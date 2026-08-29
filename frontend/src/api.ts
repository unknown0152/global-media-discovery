export type Facet = { value: string; count: number }
export type Pagination = { total: number; limit: number; offset: number; has_more: boolean }
export type Evidence = { source: string; source_record_id: string; reported_date: string; url: string | null; confidence: number; supports_selected_date: boolean; difference_days: number | null }
export type EventItem = {
  event_id: string; event_type: string; date: string; season_number: number | null; episode_number: number | null
  event_country: string | null; event_network: string | null; confidence: number; date_conflict: boolean
  title: { id: string; name: string; original_name: string | null; overview: string; language: string | null; format: string | null; status: string | null; runtime_minutes: number | null; poster_url: string | null; backdrop_url: string | null; confidence: number }
  countries: string[]; genres: string[]
  networks: Array<{ name: string; country: string | null; type: string | null; source: string }>
  external_ids: Array<{ source: string; id: string; url: string | null }>; evidence: Evidence[]
  quality_flags: Array<{ flag: string; source: string; detail: string | null }>
  aliases?: Array<{ name: string; language: string | null; source: string }>; events?: Array<Record<string, unknown>>
}
export type EventsResponse = { items: EventItem[]; pagination: Pagination; range: { from: string; to: string }; summary: { matching_events: number; date_conflicts: number } }
export type Facets = { countries: Facet[]; languages: Facet[]; networks: Facet[]; genres: Facet[]; formats: Facet[]; sources: Facet[]; event_types: Facet[] }
export type Meta = { site_name: string; updated_at: string; title_count: number; event_count: number; conflict_count: number; date_bounds: { min: string; max: string }; integrations: { seerr: { configured: boolean; public_url: string | null } } }

type APIError = { error?: { message?: string } }
export async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, headers: { Accept: 'application/json' }, cache: 'no-store' })
  const text = await response.text()
  let body: unknown
  try { body = text ? JSON.parse(text) : null } catch { throw new Error('The catalog returned an incomplete response. Please retry.') }
  if (!response.ok) throw new Error((body as APIError | null)?.error?.message ?? `Catalog request failed (${response.status})`)
  return body as T
}
export function params(values: Record<string, string | number | undefined>) { const result = new URLSearchParams(); Object.entries(values).forEach(([key, value]) => { if (value !== '' && value !== undefined) result.set(key, String(value)) }); return result.toString() }
