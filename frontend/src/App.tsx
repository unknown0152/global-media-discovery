import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useRef, useState } from 'react'
import { type EventItem, type EventsResponse, type Facet, type Facets, type Meta, getJSON, params } from './api'

type View = 'day' | 'week' | 'month' | 'next30' | 'custom'
type SearchState = { view?: View; date?: string; from?: string; to?: string; q?: string; country?: string; language?: string; network?: string; genre?: string; format?: string; source?: string; event_type?: string; confidence?: string; conflict?: string; sort?: string }
const iso = (date: Date) => date.toISOString().slice(0, 10)
const today = iso(new Date())
const addDays = (value: string, days: number) => { const d = new Date(`${value}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + days); return iso(d) }
const formatDate = (value: string, options: Intl.DateTimeFormatOptions = {}) => new Intl.DateTimeFormat(undefined, { timeZone: 'UTC', ...options }).format(new Date(`${value}T12:00:00Z`))
const eventLabel = (item: EventItem) => {
  if (item.event_type === 'series_premiere') return 'Series premiere'
  if (item.event_type === 'season_premiere') return item.season_number ? `Season ${item.season_number} premiere` : 'Season premiere'
  if (item.event_type === 'midseason_finale') return 'Mid-season finale'
  if (item.event_type === 'season_finale') return item.season_number ? `Season ${item.season_number} finale` : 'Season finale'
  if (item.event_type === 'series_finale') return 'Series finale'
  return item.event_type.replaceAll('_', ' ')
}

function rangeFor(state: SearchState) {
  const view = state.view ?? 'day'; const anchor = state.date ?? today
  if (view === 'day') return { from: anchor, to: anchor }
  if (view === 'next30') return { from: today, to: addDays(today, 29) }
  if (view === 'custom') return { from: state.from ?? anchor, to: state.to ?? anchor }
  const date = new Date(`${anchor}T12:00:00Z`)
  if (view === 'week') { const weekday = (date.getUTCDay() + 6) % 7; const from = addDays(anchor, -weekday); return { from, to: addDays(from, 6) } }
  const from = `${anchor.slice(0, 7)}-01`; const end = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)); return { from, to: iso(end) }
}

export function App() {
  const raw = useSearch({ from: '/' }) as SearchState
  const navigate = useNavigate({ from: '/' })
  const search: SearchState = { view: raw.view ?? 'day', date: raw.date ?? today, ...raw }
  const range = rangeFor(search)
  const [selected, setSelected] = useState<EventItem | null>(null)
  const [theme, setTheme] = useState(() => localStorage.getItem('gmd-theme') ?? 'system')
  const setSearch = (patch: Partial<SearchState>, replace = false) => navigate({ search: (old) => ({ ...(old as SearchState), ...patch }), replace })
  const filters = { q: search.q, country: search.country, language: search.language, network: search.network, genre: search.genre, format: search.format, source: search.source, event_type: search.event_type, confidence: search.confidence, conflict: search.conflict, sort: search.sort ?? 'date_asc' }
  const eventsURL = `/api/v1/events?${params({ from: range.from, to: range.to, ...filters, limit: 200 })}`
  const facetsURL = `/api/v1/filters?${params({ from: range.from, to: range.to })}`
  const events = useQuery({ queryKey: ['events', eventsURL], queryFn: ({ signal }) => getJSON<EventsResponse>(eventsURL, signal) })
  const facets = useQuery({ queryKey: ['facets', facetsURL], queryFn: ({ signal }) => getJSON<Facets>(facetsURL, signal) })
  const meta = useQuery({ queryKey: ['meta'], queryFn: ({ signal }) => getJSON<Meta>('/api/v1/meta', signal) })
  const detailURL = selected ? `/api/v1/titles/${selected.title.id}?${params({ event_id:selected.event_id })}` : ''
  const detail = useQuery({ queryKey: ['title', detailURL], queryFn: ({ signal }) => getJSON<EventItem>(detailURL, signal), enabled: Boolean(selected) })

  useEffect(() => { const dark = theme === 'dark' || (theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches); document.documentElement.classList.toggle('dark', dark); localStorage.setItem('gmd-theme', theme) }, [theme])
  const reset = () => navigate({ search: { view: 'day', date: today } })
  const shift = (amount: number) => { const width = Math.round((new Date(`${range.to}T12:00:00Z`).getTime() - new Date(`${range.from}T12:00:00Z`).getTime()) / 86400000) + 1; setSearch({ view: 'custom', from: addDays(range.from, amount * width), to: addDays(range.to, amount * width), date: addDays(range.from, amount * width) }) }
  const activeCount = Object.entries(filters).filter(([key, value]) => key !== 'sort' && value).length
  const heading = range.from === range.to ? formatDate(range.from, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }) : `${formatDate(range.from, { month: 'short', day: 'numeric' })} — ${formatDate(range.to, { month: 'short', day: 'numeric', year: 'numeric' })}`

  return <div className="min-h-screen">
    <header className="shell flex items-center justify-between py-5">
      <a href="/" className="brand" aria-label="Global Media Discovery home"><span>G</span><strong>Global Media<br/>Discovery</strong></a>
      <div className="flex items-center gap-3"><span className="live"><i/>LIVE CATALOG</span><select aria-label="Color theme" value={theme} onChange={(e) => setTheme(e.target.value)} className="compact"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></div>
    </header>
    <main>
      <section className="hero"><div className="shell py-12 md:py-16"><p className="eyebrow">A DATE-FIRST TELEVISION INDEX</p><h1>Find what premiered.<br/><em>Anywhere. Any day.</em></h1><p className="lede">Browse worldwide television records without popularity gates. Every provider date remains evidence—even when sources disagree.</p><div className="stats"><span><strong>{meta.data?.title_count.toLocaleString() ?? '—'}</strong> titles</span><span><strong>{meta.data?.event_count.toLocaleString() ?? '—'}</strong> dated events</span><span><strong>{meta.data?.conflict_count.toLocaleString() ?? '—'}</strong> date disagreements</span></div></div></section>
      <section className="shell controls" aria-label="Date navigation">
        <div className="preset-row">{([['day','Today'],['day','Tomorrow'],['week','This week'],['month','Month'],['next30','Next 30 days']] as const).map(([view,label]) => <button key={label} className={(search.view === view && !(label === 'Tomorrow' && search.date !== addDays(today,1)) && !(label === 'Today' && search.date !== today)) ? 'active' : ''} onClick={() => setSearch({ view, date: label === 'Tomorrow' ? addDays(today,1) : today, from: undefined, to: undefined })}>{label}</button>)}</div>
        <div className="date-row"><button onClick={() => shift(-1)} aria-label="Previous period">←</button><input type="date" aria-label="Direct date" value={search.date ?? today} onChange={(e) => setSearch({ view:'day', date:e.target.value })}/><div><span>Viewing</span><strong>{heading}</strong></div><button onClick={() => shift(1)} aria-label="Next period">→</button></div>
        <details className="custom"><summary>Custom range</summary><div><label>From<input type="date" value={range.from} onChange={(e) => setSearch({ view:'custom', from:e.target.value, to: range.to, date:e.target.value })}/></label><label>To<input type="date" value={range.to} onChange={(e) => setSearch({ view:'custom', from:range.from, to:e.target.value })}/></label></div></details>
      </section>
      <section className="shell discovery">
        <aside className="filters"><div className="filter-title"><div><span>REFINE</span><strong>Filters {activeCount ? `(${activeCount})` : ''}</strong></div><button onClick={reset}>Reset</button></div>
          <label className="searchbox"><span>Search title or alias</span><input type="search" value={search.q ?? ''} placeholder="Try an obscure title…" onChange={(e) => setSearch({ q:e.target.value || undefined }, true)}/></label>
          <Select label="Origin country" value={search.country} options={facets.data?.countries} onChange={(country) => setSearch({ country })}/>
          <Select label="Original language" value={search.language} options={facets.data?.languages} onChange={(language) => setSearch({ language })}/>
          <Select label="Network / service" value={search.network} options={facets.data?.networks} onChange={(network) => setSearch({ network })}/>
          <Select label="Genre" value={search.genre} options={facets.data?.genres} onChange={(genre) => setSearch({ genre })}/>
          <Select label="Format" value={search.format} options={facets.data?.formats} onChange={(format) => setSearch({ format })}/>
          <Select label="Source" value={search.source} options={facets.data?.sources} onChange={(source) => setSearch({ source })}/>
          <Select label="Event type" value={search.event_type} options={facets.data?.event_types} onChange={(event_type) => setSearch({ event_type })}/>
          <label>Date confidence<select value={search.confidence ?? ''} onChange={(e) => setSearch({ confidence:e.target.value || undefined })}><option value="">All levels</option><option value="high">High (85%+)</option><option value="medium">Medium (65–84%)</option><option value="low">Low (&lt;65%)</option></select></label>
          <label>Evidence agreement<select value={search.conflict ?? ''} onChange={(e) => setSearch({ conflict:e.target.value || undefined })}><option value="">Everything</option><option value="exclude">Agreed only</option><option value="only">Disagreements only</option></select></label>
          <div className="obscure"><strong>Obscure is the default.</strong><p>No popularity or vote threshold is applied.</p></div>
        </aside>
        <div className="results"><div className="results-head"><div><span>WORLDWIDE TELEVISION EVENTS</span><h2>{heading}</h2></div><label>Sort<select value={search.sort ?? 'date_asc'} onChange={(e) => setSearch({ sort:e.target.value })}><option value="date_asc">Date, then title</option><option value="date_desc">Latest first</option><option value="title_asc">Title A–Z</option><option value="confidence_desc">Confidence</option></select></label></div>
          {events.isPending && <State title="Reading the worldwide calendar…"/>}
          {events.isError && <State error title="The catalog could not be loaded." body={events.error.message} action={() => { void events.refetch() }}/>} 
          {events.data && <><p className="match-count">{events.data.pagination.total.toLocaleString()} matching events · {events.data.summary.date_conflicts.toLocaleString()} date disagreements</p><VirtualList items={events.data.items} onOpen={setSelected}/>{events.data.pagination.has_more && <p className="limit-note">Showing the first 200 matches. Narrow the range or filters for more precise results.</p>}</>}
        </div>
      </section>
    </main>
    <footer className="shell"><span>Global Media Discovery · read-only public catalog</span><span>React 19.2 · TanStack · Go 1.27</span></footer>
    {selected && <Detail item={detail.data} pending={detail.isPending} error={detail.error?.message} seerr={meta.data?.integrations.seerr} close={() => setSelected(null)}/>} 
  </div>
}

function Select({ label, value, options = [], onChange }: { label:string; value?:string; options?:Facet[]; onChange:(value:string|undefined)=>void }) {
  const allLabel: Record<string, string> = { 'Origin country':'All countries', 'Original language':'All languages', 'Network / service':'All networks', Genre:'All genres', Format:'All formats', Source:'All sources', 'Event type':'All event types' }
  return <label>{label}<select value={value ?? ''} onChange={(e) => onChange(e.target.value || undefined)}><option value="">{allLabel[label] ?? `All ${label.toLowerCase()}`}</option>{options.map((option) => <option key={option.value} value={option.value}>{option.value} ({option.count})</option>)}</select></label>
}
function State({ title, body, error, action }: { title:string; body?:string; error?:boolean; action?:()=>void }) { return <div className={`state ${error?'error':''}`} role={error?'alert':'status'}><strong>{title}</strong>{body&&<p>{body}</p>}{action&&<button onClick={action}>Try again</button>}</div> }

function VirtualList({ items, onOpen }: { items:EventItem[]; onOpen:(item:EventItem)=>void }) {
  const parent = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({ count:items.length, getScrollElement:()=>parent.current, estimateSize:()=>178, overscan:5, measureElement:(el)=>el.getBoundingClientRect().height })
  if (!items.length) return <State title="Nothing matched." body="Try widening the date range or resetting filters. Zero-popularity titles are already included."/>
  return <div ref={parent} className="virtual-scroll"><div style={{ height:virtualizer.getTotalSize(), position:'relative' }}>{virtualizer.getVirtualItems().map((row) => { const item=items[row.index]; if (!item) return null; return <div key={item.event_id} ref={virtualizer.measureElement} data-index={row.index} className="virtual-row" style={{ transform:`translateY(${row.start}px)` }}><Card item={item} open={()=>onOpen(item)}/></div> })}</div></div>
}
function Card({ item, open }: { item:EventItem; open:()=>void }) { return <article className="card"><button className="poster" onClick={open} aria-label={`Open ${item.title.name}`}>{item.title.poster_url?<img src={item.title.poster_url} alt="" loading="lazy" onError={(e)=>e.currentTarget.hidden=true}/>:<span>{item.title.name.slice(0,1)}</span>}</button><div className="card-body"><button className="title" onClick={open}><h3>{item.title.name}</h3></button>{item.title.original_name&&item.title.original_name!==item.title.name&&<p className="original">{item.title.original_name}</p>}<p className="meta">{[item.countries.join(', '),item.title.language,item.title.format,item.networks.slice(0,2).map(n=>n.name).join(', ')].filter(Boolean).join(' · ')}</p><p className="overview">{item.title.overview || 'No overview is currently available.'}</p><div className="tags"><span>{eventLabel(item)}</span>{item.genres.slice(0,3).map(g=><span key={g}>{g}</span>)}{[...new Set(item.evidence.map(e=>e.source))].map(s=><b key={s}>{s}</b>)}<strong className={item.date_conflict?'conflict':''}>{Math.round(item.confidence*100)}% {item.date_conflict?'· dates differ':''}</strong></div></div><time dateTime={item.date}><b>{item.date.slice(8)}</b><span>{formatDate(item.date,{month:'short'})}</span></time></article> }

function Detail({ item, pending, error, seerr, close }: { item?:EventItem; pending:boolean; error?:string; seerr?:Meta['integrations']['seerr']; close:()=>void }) {
  useEffect(()=>{ const handler=(e:KeyboardEvent)=>{if(e.key==='Escape')close()};addEventListener('keydown',handler);return()=>removeEventListener('keydown',handler)},[close])
  const tmdb=item?.external_ids.find(x=>x.source==='tmdb'&&/^\d+$/.test(x.id)); const seerrURL=seerr?.configured&&seerr.public_url&&tmdb?`${seerr.public_url.replace(/\/$/,'')}/tv/${tmdb.id}`:null
  return <div className="modal-backdrop" role="presentation" onMouseDown={(e)=>{if(e.target===e.currentTarget)close()}}><section className="modal" role="dialog" aria-modal="true" aria-label={item?.title.name??'Title details'}><button className="close" onClick={close} aria-label="Close details">×</button>{pending&&<State title="Resolving source evidence…"/>}{error&&<State error title="Could not open this title." body={error}/>} {item&&<><div className="detail-hero">{item.title.backdrop_url&&<img src={item.title.backdrop_url} alt=""/>}<div><div className="detail-poster">{item.title.poster_url?<img src={item.title.poster_url} alt=""/>:<span>{item.title.name.slice(0,1)}</span>}</div><h2>{item.title.name}</h2></div></div><div className="detail-grid"><div><p className="detail-overview">{item.title.overview||'No overview has been supplied by the current metadata sources.'}</p><h3>Date evidence</h3>{item.date_conflict&&<p className="warning">Sources report different dates. Every reported date is retained.</p>}<div className="evidence">{item.evidence.map(e=><div key={`${e.source}-${e.source_record_id}`}><b>{e.source.toUpperCase()}</b><span>{formatDate(e.reported_date,{month:'long',day:'numeric',year:'numeric'})}</span>{e.url?<a href={e.url} target="_blank" rel="noreferrer">Source ↗</a>:<i/>}</div>)}</div><h3>External records</h3><div className="links">{item.external_ids.filter(x=>x.url).map(x=><a key={`${x.source}-${x.id}`} href={x.url!} target="_blank" rel="noreferrer">{x.source.toUpperCase()} · {x.id}</a>)}</div>{seerrURL&&<a className="seerr" href={seerrURL} target="_blank" rel="noreferrer">Open in Seerr ↗</a>}</div><aside>{[['Event type',eventLabel(item)],['Event date',item.date],['Origin',item.countries.join(', ')||'Unknown'],['Language',item.title.language||'Unknown'],['Format',item.title.format||'Unknown'],['Network / service',item.networks.map(n=>n.name).join(', ')||'Unknown'],['Genres',item.genres.join(', ')||'Unclassified'],['Confidence',`${Math.round(item.confidence*100)}%`]].map(([k,v])=><div key={k}><span>{k}</span><strong>{v}</strong></div>)}</aside></div></>}</section></div>
}
