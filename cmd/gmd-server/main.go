package main

import (
	"context"
	"database/sql"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"

	_ "modernc.org/sqlite"
)

//go:embed all:dist
var frontend embed.FS

const version = "2.1.0"

type app struct {
	db           *sql.DB
	databasePath string
	siteName     string
	seerrURL     string
	maxDays      int
	maxPageSize  int
	static       http.Handler
	logger       *slog.Logger
}

type apiError struct {
	Status  int
	Code    string
	Message string
}

func (e *apiError) Error() string { return e.Message }

type filters struct {
	from, to, q, country, language, network, genre, format, source, eventType, confidence, conflict, sort string
	limit, offset                                                                                         int
}

func main() {
	databasePath := env("GMD_DATABASE_PATH", "/data/catalog.sqlite3")
	db, err := sql.Open("sqlite", "file:"+databasePath+"?mode=ro&_pragma=query_only(1)&_pragma=foreign_keys(1)&_pragma=busy_timeout(5000)")
	if err != nil {
		panic(err)
	}
	// The collector atomically replaces the file. Reopen for every request so a
	// pooled descriptor never pins an older published catalog.
	db.SetMaxIdleConns(0)
	db.SetMaxOpenConns(16)

	dist, err := fs.Sub(frontend, "dist")
	if err != nil {
		panic(err)
	}
	a := &app{
		db: db, databasePath: databasePath,
		siteName:    env("GMD_SITE_NAME", "Global Media Discovery"),
		seerrURL:    cleanPublicURL(os.Getenv("GMD_SEERR_PUBLIC_URL")),
		maxDays:     envInt("GMD_MAX_QUERY_DAYS", 366),
		maxPageSize: envInt("GMD_MAX_PAGE_SIZE", 200),
		static:      http.FileServerFS(dist), logger: slog.New(slog.NewJSONHandler(os.Stdout, nil)),
	}

	server := &http.Server{
		Addr: env("GMD_LISTEN", ":8080"), Handler: a,
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second,
		WriteTimeout: 30 * time.Second, IdleTimeout: 60 * time.Second,
		MaxHeaderBytes: 1 << 20,
	}
	go func() {
		a.logger.Info("gmd server starting", "version", version, "address", server.Addr)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			panic(err)
		}
	}()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	_ = server.Shutdown(ctx)
	_ = db.Close()
}

func (a *app) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	requestID := fmt.Sprintf("%x", time.Now().UnixNano())
	w.Header().Set("X-Request-ID", requestID)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if strings.HasPrefix(r.URL.Path, "/api/") {
		a.serveAPI(w, r, requestID)
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Cache-Control", "no-cache")
	if strings.HasSuffix(r.URL.Path, ".webmanifest") {
		w.Header().Set("Content-Type", "application/manifest+json; charset=utf-8")
	}
	if r.URL.Path == "/" || !strings.Contains(path.Base(r.URL.Path), ".") {
		r.URL.Path = "/"
	}
	a.static.ServeHTTP(w, r)
}

func (a *app) serveAPI(w http.ResponseWriter, r *http.Request, requestID string) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		a.writeJSON(w, r, http.StatusMethodNotAllowed, map[string]any{"error": map[string]any{"code": "method_not_allowed", "message": "This public API is read-only.", "request_id": requestID}})
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	var payload any
	var err error
	switch r.URL.Path {
	case "/api/v1/health":
		payload, err = a.health(ctx)
	case "/api/v1/meta":
		payload, err = a.meta(ctx)
	case "/api/v1/status":
		payload, err = a.status(ctx)
	case "/api/v1/stats":
		payload, err = a.stats(ctx)
	case "/api/v1/coverage":
		payload, err = a.coverage(ctx)
	case "/api/v1/events", "/api/v1/date-range":
		var f filters
		f, err = a.parseFilters(r)
		if err == nil {
			payload, err = a.events(ctx, f)
		}
	case "/api/v1/facets", "/api/v1/filters":
		var from, to string
		from, to, err = a.dateRange(r)
		if err == nil {
			payload, err = a.facets(ctx, from, to)
		}
	case "/api/v1/search":
		payload, err = a.search(ctx, r)
	case "/api/v1/calendar":
		payload, err = a.calendar(ctx, r)
	case "/api/v1/credits":
		payload = credits()
	default:
		if strings.HasPrefix(r.URL.Path, "/api/v1/titles/") {
			payload, err = a.title(ctx, strings.TrimPrefix(r.URL.Path, "/api/v1/titles/"), r.URL.Query().Get("event_id"))
		} else {
			err = &apiError{404, "not_found", "API route not found."}
		}
	}
	if err != nil {
		var ae *apiError
		if errors.As(err, &ae) {
			a.writeJSON(w, r, ae.Status, map[string]any{"error": map[string]any{"code": ae.Code, "message": ae.Message, "request_id": requestID}})
			return
		}
		a.logger.Error("api request failed", "path", r.URL.Path, "request_id", requestID, "error", err)
		a.writeJSON(w, r, 500, map[string]any{"error": map[string]any{"code": "internal_error", "message": "The catalog API encountered an unexpected error.", "request_id": requestID}})
		return
	}
	a.writeJSON(w, r, 200, payload)
}

func (a *app) writeJSON(w http.ResponseWriter, r *http.Request, status int, payload any) {
	body, err := json.Marshal(payload)
	if err != nil {
		http.Error(w, `{"error":{"code":"encoding_error"}}`, 500)
		return
	}
	w.Header().Set("Content-Length", strconv.Itoa(len(body)))
	w.WriteHeader(status)
	if r.Method != http.MethodHead {
		_, _ = w.Write(body)
	}
}

func (a *app) health(ctx context.Context) (any, error) {
	var one int
	if err := a.db.QueryRowContext(ctx, "SELECT 1").Scan(&one); err != nil {
		return nil, &apiError{503, "catalog_unavailable", "The catalog is not ready."}
	}
	counts, err := a.counts(ctx)
	if err != nil {
		return nil, err
	}
	meta, _ := a.metaValues(ctx)
	return map[string]any{"status": "ok", "database": "ready", "updated_at": meta["updated_at"], "catalog_version": meta["catalog_version"], "title_count": counts["title_count"], "event_count": counts["event_count"], "source_record_count": counts["source_record_count"], "conflict_count": counts["conflict_count"], "server_version": version}, nil
}

func (a *app) meta(ctx context.Context) (any, error) {
	counts, err := a.counts(ctx)
	if err != nil {
		return nil, err
	}
	meta, err := a.metaValues(ctx)
	if err != nil {
		return nil, err
	}
	var min, max sql.NullString
	if err := a.db.QueryRowContext(ctx, "SELECT MIN(event_date), MAX(event_date) FROM events").Scan(&min, &max); err != nil {
		return nil, err
	}
	formats, err := a.stringList(ctx, "SELECT DISTINCT format FROM titles WHERE format != '' ORDER BY format COLLATE NOCASE")
	if err != nil {
		return nil, err
	}
	run, _ := a.lastRun(ctx)
	return map[string]any{"site_name": first(meta["site_name"], a.siteName), "updated_at": meta["updated_at"], "catalog_version": toInt(meta["catalog_version"]), "date_bounds": map[string]any{"min": nullable(min), "max": nullable(max)}, "formats": formats, "title_count": counts["title_count"], "event_count": counts["event_count"], "source_record_count": counts["source_record_count"], "conflict_count": counts["conflict_count"], "last_run": run, "integrations": map[string]any{"seerr": map[string]any{"configured": a.seerrURL != "", "mode": "authenticated_handoff", "public_url": nullIfEmpty(a.seerrURL)}}}, nil
}

func (a *app) status(ctx context.Context) (any, error) {
	rows, err := a.db.QueryContext(ctx, "SELECT source,last_success_at,last_attempt_at,status FROM collection_state WHERE source IN ('tmdb','tvdb','tvmaze','simkl') ORDER BY source")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	sources := []any{}
	for rows.Next() {
		var s, st string
		var ok, attempt sql.NullString
		if err := rows.Scan(&s, &ok, &attempt, &st); err != nil {
			return nil, err
		}
		sources = append(sources, map[string]any{"source": s, "last_success_at": nullable(ok), "last_attempt_at": nullable(attempt), "status": st})
	}
	meta, _ := a.metaValues(ctx)
	run, _ := a.lastRun(ctx)
	return map[string]any{"status": "ok", "updated_at": meta["updated_at"], "sources": sources, "last_run": run}, rows.Err()
}

func (a *app) stats(ctx context.Context) (any, error) {
	c, err := a.counts(ctx)
	if err != nil {
		return nil, err
	}
	var ev int
	var min, max sql.NullString
	if err = a.db.QueryRowContext(ctx, "SELECT COUNT(*),MIN(event_date),MAX(event_date) FROM event_evidence JOIN events ON events.id=event_evidence.event_id").Scan(&ev, &min, &max); err != nil {
		return nil, err
	}
	bySource, err := a.facetsQuery(ctx, "SELECT source,COUNT(*) FROM source_records GROUP BY source ORDER BY source")
	if err != nil {
		return nil, err
	}
	byType, err := a.facetsQuery(ctx, "SELECT event_type,COUNT(*) FROM events GROUP BY event_type ORDER BY COUNT(*) DESC")
	if err != nil {
		return nil, err
	}
	c["evidence_count"] = ev
	c["date_bounds"] = map[string]any{"min": nullable(min), "max": nullable(max)}
	c["by_source"] = bySource
	c["by_event_type"] = byType
	return c, nil
}

func (a *app) counts(ctx context.Context) (map[string]any, error) {
	var t, e, s, c int
	err := a.db.QueryRowContext(ctx, "SELECT (SELECT COUNT(*) FROM titles),(SELECT COUNT(*) FROM events),(SELECT COUNT(*) FROM source_records),(SELECT COUNT(*) FROM events WHERE date_conflict=1)").Scan(&t, &e, &s, &c)
	return map[string]any{"title_count": t, "event_count": e, "source_record_count": s, "conflict_count": c}, err
}

func (a *app) events(ctx context.Context, f filters) (any, error) {
	clauses := []string{"e.event_date BETWEEN ? AND ?"}
	args := []any{f.from, f.to}
	if f.q != "" {
		like := "%" + escapeLike(f.q) + "%"
		clauses = append(clauses, "(t.canonical_title LIKE ? ESCAPE '\\' OR t.original_title LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM aliases a WHERE a.title_id=t.id AND a.alias LIKE ? ESCAPE '\\'))")
		args = append(args, like, like, like)
	}
	if f.country != "" {
		clauses = append(clauses, "EXISTS (SELECT 1 FROM countries c WHERE c.title_id=t.id AND c.country_code=?)")
		args = append(args, f.country)
	}
	if f.language != "" {
		clauses = append(clauses, "t.original_language=?")
		args = append(args, f.language)
	}
	if f.network != "" {
		clauses = append(clauses, "EXISTS (SELECT 1 FROM networks n WHERE n.title_id=t.id AND n.network_name=?)")
		args = append(args, f.network)
	}
	if f.genre != "" {
		clauses = append(clauses, "EXISTS (SELECT 1 FROM genres g WHERE g.title_id=t.id AND g.genre=?)")
		args = append(args, f.genre)
	}
	if f.format != "" {
		clauses = append(clauses, "t.format=?")
		args = append(args, f.format)
	}
	if f.source != "" {
		clauses = append(clauses, "EXISTS (SELECT 1 FROM event_evidence x WHERE x.event_id=e.id AND x.source=?)")
		args = append(args, f.source)
	}
	if f.eventType != "" {
		clauses = append(clauses, "e.event_type=?")
		args = append(args, f.eventType)
	}
	switch f.confidence {
	case "high":
		clauses = append(clauses, "e.confidence>=0.85")
	case "medium":
		clauses = append(clauses, "e.confidence>=0.65 AND e.confidence<0.85")
	case "low":
		clauses = append(clauses, "e.confidence<0.65")
	}
	if f.conflict == "only" {
		clauses = append(clauses, "e.date_conflict=1")
	} else if f.conflict == "exclude" {
		clauses = append(clauses, "e.date_conflict=0")
	}
	where := strings.Join(clauses, " AND ")
	var total, conflicts int
	if err := a.db.QueryRowContext(ctx, "SELECT COUNT(*),COALESCE(SUM(e.date_conflict),0) FROM events e JOIN titles t ON t.id=e.title_id WHERE "+where, args...).Scan(&total, &conflicts); err != nil {
		return nil, err
	}
	order := map[string]string{"date_asc": "e.event_date,t.canonical_title COLLATE NOCASE", "date_desc": "e.event_date DESC,t.canonical_title COLLATE NOCASE", "title_asc": "t.canonical_title COLLATE NOCASE,e.event_date", "confidence_desc": "e.confidence DESC,e.event_date,t.canonical_title COLLATE NOCASE"}[f.sort]
	query := `SELECT e.id,e.event_type,e.event_date,e.season_number,e.episode_number,e.country_code,e.network_name,e.confidence,e.date_conflict,t.id,t.canonical_title,t.original_title,t.overview,t.original_language,t.format,t.status,t.runtime_minutes,t.poster_url,t.backdrop_url,t.confidence,
	COALESCE((SELECT json_group_array(country_code) FROM (SELECT DISTINCT country_code FROM countries WHERE title_id=t.id ORDER BY country_code)),'[]'),
	COALESCE((SELECT json_group_array(genre) FROM (SELECT DISTINCT genre FROM genres WHERE title_id=t.id ORDER BY genre COLLATE NOCASE)),'[]'),
	COALESCE((SELECT json_group_array(json_object('name',network_name,'country',network_country,'type',network_type,'source',source)) FROM (SELECT DISTINCT network_name,network_country,network_type,source FROM networks WHERE title_id=t.id ORDER BY network_name COLLATE NOCASE)),'[]'),
	COALESCE((SELECT json_group_array(json_object('source',source,'id',external_id,'url',source_url)) FROM identity_keys WHERE title_id=t.id),'[]'),
	COALESCE((SELECT json_group_array(json_object('source',source,'source_record_id',source_record_id,'reported_date',reported_date,'url',source_url,'confidence',confidence,'supports_selected_date',reported_date=e.event_date,'difference_days',CAST(julianday(reported_date)-julianday(e.event_date) AS INTEGER))) FROM event_evidence WHERE event_id=e.id),'[]'),
	COALESCE((SELECT json_group_array(json_object('flag',flag,'source',source,'detail',detail)) FROM quality_flags WHERE title_id=t.id),'[]')
	FROM events e JOIN titles t ON t.id=e.title_id WHERE ` + where + ` ORDER BY ` + order + ` LIMIT ? OFFSET ?`
	qargs := append(append([]any{}, args...), f.limit, f.offset)
	rows, err := a.db.QueryContext(ctx, query, qargs...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []any{}
	for rows.Next() {
		item, err := scanEvent(rows)
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return map[string]any{"items": items, "pagination": map[string]any{"total": total, "limit": f.limit, "offset": f.offset, "has_more": f.offset+len(items) < total}, "range": map[string]any{"from": f.from, "to": f.to}, "summary": map[string]any{"matching_events": total, "date_conflicts": conflicts}}, rows.Err()
}

type scanner interface{ Scan(...any) error }

func scanEvent(s scanner) (map[string]any, error) {
	var id, typ, date, titleID, name string
	var season, episode int
	var country, network, original, overview, language, format, status, poster, backdrop sql.NullString
	var runtime sql.NullInt64
	var confidence, titleConfidence float64
	var conflict int
	var countries, genres, networks, external, evidence, flags string
	err := s.Scan(&id, &typ, &date, &season, &episode, &country, &network, &confidence, &conflict, &titleID, &name, &original, &overview, &language, &format, &status, &runtime, &poster, &backdrop, &titleConfidence, &countries, &genres, &networks, &external, &evidence, &flags)
	if err != nil {
		return nil, err
	}
	item := map[string]any{"event_id": id, "event_type": typ, "date": date, "season_number": nullableNumber(season), "episode_number": nullableNumber(episode), "event_country": nullable(country), "event_network": nullable(network), "confidence": round3(confidence), "date_conflict": conflict == 1, "title": map[string]any{"id": titleID, "name": name, "original_name": nullable(original), "overview": value(overview), "language": nullable(language), "format": nullable(format), "status": nullable(status), "runtime_minutes": nullableInt(runtime), "poster_url": nullable(poster), "backdrop_url": nullable(backdrop), "confidence": round3(titleConfidence)}}
	for key, raw := range map[string]string{"countries": countries, "genres": genres, "networks": networks, "external_ids": external, "evidence": evidence, "quality_flags": flags} {
		var v any
		if err := json.Unmarshal([]byte(raw), &v); err != nil {
			return nil, err
		}
		item[key] = v
	}
	return item, nil
}

func (a *app) title(ctx context.Context, id, eventID string) (any, error) {
	if ok, _ := regexp.MatchString(`^[a-z0-9_]{8,80}$`, id); !ok {
		return nil, &apiError{400, "invalid_title_id", "Invalid title identifier."}
	}
	if eventID != "" {
		if ok, _ := regexp.MatchString(`^[a-z0-9_]{8,80}$`, eventID); !ok {
			return nil, &apiError{400, "invalid_event_id", "Invalid event identifier."}
		}
	}
	row := a.db.QueryRowContext(ctx, `SELECT COALESCE(e.id,''),COALESCE(e.event_type,''),COALESCE(e.event_date,''),COALESCE(e.season_number,-1),COALESCE(e.episode_number,-1),e.country_code,e.network_name,COALESCE(e.confidence,0),COALESCE(e.date_conflict,0),t.id,t.canonical_title,t.original_title,t.overview,t.original_language,t.format,t.status,t.runtime_minutes,t.poster_url,t.backdrop_url,t.confidence,
	COALESCE((SELECT json_group_array(country_code) FROM (SELECT DISTINCT country_code FROM countries WHERE title_id=t.id ORDER BY country_code)),'[]'),COALESCE((SELECT json_group_array(genre) FROM (SELECT DISTINCT genre FROM genres WHERE title_id=t.id ORDER BY genre)),'[]'),COALESCE((SELECT json_group_array(json_object('name',network_name,'country',network_country,'type',network_type,'source',source)) FROM networks WHERE title_id=t.id),'[]'),COALESCE((SELECT json_group_array(json_object('source',source,'id',external_id,'url',source_url)) FROM identity_keys WHERE title_id=t.id),'[]'),COALESCE((SELECT json_group_array(json_object('source',source,'source_record_id',source_record_id,'reported_date',reported_date,'url',source_url,'confidence',confidence,'supports_selected_date',reported_date=e.event_date,'difference_days',CAST(julianday(reported_date)-julianday(e.event_date) AS INTEGER))) FROM event_evidence WHERE event_id=e.id),'[]'),COALESCE((SELECT json_group_array(json_object('flag',flag,'source',source,'detail',detail)) FROM quality_flags WHERE title_id=t.id),'[]') FROM titles t LEFT JOIN events e ON e.title_id=t.id AND ((? != '' AND e.id=?) OR (? = '' AND e.event_type='series_premiere')) WHERE t.id=? ORDER BY e.event_date LIMIT 1`, eventID, eventID, eventID, id)
	item, err := scanEvent(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, &apiError{404, "not_found", "Title not found."}
	}
	if err != nil {
		return nil, err
	}
	if eventID != "" && item["event_id"] == "" {
		return nil, &apiError{404, "not_found", "Event not found for this title."}
	}
	aliases, err := a.jsonRows(ctx, "SELECT alias,language,source FROM aliases WHERE title_id=? ORDER BY alias COLLATE NOCASE", id, []string{"name", "language", "source"})
	if err != nil {
		return nil, err
	}
	item["aliases"] = aliases
	events, err := a.jsonRows(ctx, "SELECT id,event_type,event_date,season_number,episode_number,country_code,network_name,confidence,date_conflict FROM events WHERE title_id=? ORDER BY event_date,event_type", id, []string{"id", "type", "date", "season_number", "episode_number", "country", "network", "confidence", "date_conflict"})
	if err != nil {
		return nil, err
	}
	item["events"] = events
	return item, nil
}

func (a *app) facets(ctx context.Context, from, to string) (any, error) {
	queries := map[string]string{"countries": "SELECT c.country_code,COUNT(DISTINCT e.id) FROM events e JOIN countries c ON c.title_id=e.title_id WHERE e.event_date BETWEEN ? AND ? GROUP BY c.country_code ORDER BY 2 DESC,1", "languages": "SELECT t.original_language,COUNT(DISTINCT e.id) FROM events e JOIN titles t ON t.id=e.title_id WHERE e.event_date BETWEEN ? AND ? AND t.original_language!='' GROUP BY t.original_language ORDER BY 2 DESC,1", "networks": "SELECT n.network_name,COUNT(DISTINCT e.id) FROM events e JOIN networks n ON n.title_id=e.title_id WHERE e.event_date BETWEEN ? AND ? GROUP BY n.network_name ORDER BY 2 DESC,1 LIMIT 250", "genres": "SELECT g.genre,COUNT(DISTINCT e.id) FROM events e JOIN genres g ON g.title_id=e.title_id WHERE e.event_date BETWEEN ? AND ? GROUP BY g.genre ORDER BY 2 DESC,1", "formats": "SELECT t.format,COUNT(DISTINCT e.id) FROM events e JOIN titles t ON t.id=e.title_id WHERE e.event_date BETWEEN ? AND ? GROUP BY t.format ORDER BY 2 DESC,1", "sources": "SELECT x.source,COUNT(DISTINCT x.event_id) FROM event_evidence x JOIN events e ON e.id=x.event_id WHERE e.event_date BETWEEN ? AND ? GROUP BY x.source ORDER BY 2 DESC,1", "event_types": "SELECT e.event_type,COUNT(*) FROM events e WHERE e.event_date BETWEEN ? AND ? GROUP BY e.event_type ORDER BY 2 DESC,1"}
	out := map[string]any{}
	for key, q := range queries {
		v, err := a.facetsQuery(ctx, q, from, to)
		if err != nil {
			return nil, err
		}
		out[key] = v
	}
	return out, nil
}

func (a *app) search(ctx context.Context, r *http.Request) (any, error) {
	q, err := bounded(r, "q", 200, true)
	if err != nil {
		return nil, err
	}
	limit, err := intParam(r, "limit", 40, 1, a.maxPageSize)
	if err != nil {
		return nil, err
	}
	offset, err := intParam(r, "offset", 0, 0, 1000000)
	if err != nil {
		return nil, err
	}
	like := "%" + escapeLike(q) + "%"
	args := []any{like, like, like}
	where := "t.canonical_title LIKE ? ESCAPE '\\' OR t.original_title LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM aliases a WHERE a.title_id=t.id AND a.alias LIKE ? ESCAPE '\\')"
	var total int
	if err = a.db.QueryRowContext(ctx, "SELECT COUNT(*) FROM titles t WHERE "+where, args...).Scan(&total); err != nil {
		return nil, err
	}
	rows, err := a.db.QueryContext(ctx, "SELECT t.id,t.canonical_title,t.original_title,t.original_language,t.format,t.poster_url,t.confidence,MIN(e.event_date),MAX(e.date_conflict) FROM titles t LEFT JOIN events e ON e.title_id=t.id WHERE "+where+" GROUP BY t.id ORDER BY t.canonical_title COLLATE NOCASE LIMIT ? OFFSET ?", append(args, limit, offset)...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []any{}
	for rows.Next() {
		var id, name string
		var original, lang, format, poster, firstDate sql.NullString
		var conf float64
		var conflict sql.NullInt64
		if err = rows.Scan(&id, &name, &original, &lang, &format, &poster, &conf, &firstDate, &conflict); err != nil {
			return nil, err
		}
		items = append(items, map[string]any{"id": id, "name": name, "original_name": nullable(original), "language": nullable(lang), "format": nullable(format), "poster_url": nullable(poster), "confidence": round3(conf), "first_event_date": nullable(firstDate), "date_conflict": conflict.Valid && conflict.Int64 == 1})
	}
	return map[string]any{"query": q, "items": items, "pagination": map[string]any{"total": total, "limit": limit, "offset": offset, "has_more": offset+len(items) < total}}, rows.Err()
}

func (a *app) calendar(ctx context.Context, r *http.Request) (any, error) {
	m := r.URL.Query().Get("month")
	if m == "" {
		m = time.Now().Format("2006-01")
	}
	start, err := time.Parse("2006-01", m)
	if err != nil {
		return nil, &apiError{400, "invalid_month", "month must use YYYY-MM."}
	}
	end := start.AddDate(0, 1, 0)
	rows, err := a.db.QueryContext(ctx, "SELECT event_date,COUNT(*),COALESCE(SUM(date_conflict),0) FROM events WHERE event_date>=? AND event_date<? GROUP BY event_date ORDER BY event_date", start.Format("2006-01-02"), end.Format("2006-01-02"))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	days := []any{}
	for rows.Next() {
		var d string
		var c, x int
		if err = rows.Scan(&d, &c, &x); err != nil {
			return nil, err
		}
		days = append(days, map[string]any{"date": d, "count": c, "conflicts": x})
	}
	return map[string]any{"month": m, "days": days}, rows.Err()
}

func (a *app) coverage(ctx context.Context) (any, error) {
	c, err := a.counts(ctx)
	if err != nil {
		return nil, err
	}
	var min, max sql.NullString
	var days, evidence int
	if err = a.db.QueryRowContext(ctx, "SELECT MIN(event_date),MAX(event_date),COUNT(DISTINCT event_date),(SELECT COUNT(*) FROM event_evidence) FROM events").Scan(&min, &max, &days, &evidence); err != nil {
		return nil, err
	}
	years, err := a.jsonRowsNoArg(ctx, `SELECT substr(e.event_date,1,4),COUNT(DISTINCT e.title_id),COUNT(*),SUM((SELECT COUNT(*) FROM event_evidence x WHERE x.event_id=e.id)),COUNT(DISTINCT e.event_date),SUM(e.date_conflict) FROM events e GROUP BY substr(e.event_date,1,4) ORDER BY 1`, []string{"year", "title_count", "event_count", "evidence_count", "active_day_count", "conflict_count"})
	if err != nil {
		return nil, err
	}
	sources, err := a.jsonRowsNoArg(ctx, `SELECT source,COUNT(DISTINCT event_id),COUNT(*),MIN(reported_date),MAX(reported_date) FROM event_evidence GROUP BY source ORDER BY source`, []string{"source", "event_count", "evidence_count", "reported_date_min", "reported_date_max"})
	if err != nil {
		return nil, err
	}
	countries, err := a.jsonRowsNoArg(ctx, `SELECT c.country_code,COUNT(DISTINCT c.title_id),COUNT(DISTINCT e.id) FROM countries c JOIN events e ON e.title_id=c.title_id GROUP BY c.country_code ORDER BY 3 DESC,1`, []string{"country", "title_count", "event_count"})
	if err != nil {
		return nil, err
	}
	meta, _ := a.metaValues(ctx)
	c["scope"] = "Observed provider records, not a claim of complete worldwide television coverage. Sparse dates can mean no provider report."
	c["updated_at"] = meta["updated_at"]
	c["date_bounds"] = map[string]any{"min": nullable(min), "max": nullable(max)}
	c["active_day_count"] = days
	c["evidence_count"] = evidence
	c["years"] = years
	c["sources"] = sources
	c["countries"] = countries
	return c, nil
}

func (a *app) parseFilters(r *http.Request) (filters, error) {
	from, to, err := a.dateRange(r)
	if err != nil {
		return filters{}, err
	}
	f := filters{from: from, to: to}
	if f.q, err = bounded(r, "q", 200, false); err != nil {
		return f, err
	}
	f.country = strings.ToUpper(r.URL.Query().Get("country"))
	if f.country != "" && !regexp.MustCompile(`^[A-Z]{2}$`).MatchString(f.country) {
		return f, &apiError{400, "invalid_country", "country must be an ISO alpha-2 code."}
	}
	f.language = strings.ToLower(r.URL.Query().Get("language"))
	f.network = r.URL.Query().Get("network")
	f.genre = r.URL.Query().Get("genre")
	f.format = r.URL.Query().Get("format")
	f.source = strings.ToLower(r.URL.Query().Get("source"))
	f.eventType = strings.ToLower(r.URL.Query().Get("event_type"))
	f.confidence = strings.ToLower(r.URL.Query().Get("confidence"))
	if !oneOf(f.confidence, "", "high", "medium", "low") {
		return f, &apiError{400, "invalid_confidence_filter", "confidence must be high, medium, or low."}
	}
	f.conflict = strings.ToLower(r.URL.Query().Get("conflict"))
	if !oneOf(f.conflict, "", "only", "exclude") {
		return f, &apiError{400, "invalid_conflict_filter", "conflict must be only or exclude."}
	}
	f.sort = strings.ToLower(r.URL.Query().Get("sort"))
	if f.sort == "" {
		f.sort = "date_asc"
	}
	if !oneOf(f.sort, "date_asc", "date_desc", "title_asc", "confidence_desc") {
		return f, &apiError{400, "invalid_sort", "Unsupported sort order."}
	}
	f.limit, err = intParam(r, "limit", 60, 1, a.maxPageSize)
	if err != nil {
		return f, err
	}
	f.offset, err = intParam(r, "offset", 0, 0, 1000000)
	return f, err
}

func (a *app) dateRange(r *http.Request) (string, string, error) {
	from := r.URL.Query().Get("from")
	to := r.URL.Query().Get("to")
	today := time.Now().UTC()
	if from == "" {
		from = today.Format("2006-01-02")
	}
	if to == "" {
		to = from
	}
	f, err := time.Parse("2006-01-02", from)
	if err != nil {
		return "", "", &apiError{400, "invalid_from", "from must be a valid YYYY-MM-DD date."}
	}
	t, err := time.Parse("2006-01-02", to)
	if err != nil {
		return "", "", &apiError{400, "invalid_to", "to must be a valid YYYY-MM-DD date."}
	}
	if t.Before(f) {
		return "", "", &apiError{400, "invalid_date_range", "to must be on or after from."}
	}
	if int(t.Sub(f).Hours()/24)+1 > a.maxDays {
		return "", "", &apiError{400, "date_range_too_large", fmt.Sprintf("Date ranges are limited to %d days.", a.maxDays)}
	}
	return from, to, nil
}

func (a *app) metaValues(ctx context.Context) (map[string]string, error) {
	rows, err := a.db.QueryContext(ctx, "SELECT key,value FROM meta")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]string{}
	for rows.Next() {
		var k, v string
		if err = rows.Scan(&k, &v); err != nil {
			return nil, err
		}
		out[k] = v
	}
	return out, rows.Err()
}
func (a *app) lastRun(ctx context.Context) (any, error) {
	var started, status, sources, metrics string
	var finished sql.NullString
	err := a.db.QueryRowContext(ctx, "SELECT started_at,finished_at,status,sources_json,metrics_json FROM runs ORDER BY id DESC LIMIT 1").Scan(&started, &finished, &status, &sources, &metrics)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var sj, mj any
	_ = json.Unmarshal([]byte(sources), &sj)
	_ = json.Unmarshal([]byte(metrics), &mj)
	return map[string]any{"started_at": started, "finished_at": nullable(finished), "status": status, "sources": sj, "metrics": mj}, nil
}
func (a *app) stringList(ctx context.Context, q string, args ...any) ([]string, error) {
	rows, err := a.db.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []string{}
	for rows.Next() {
		var v string
		if err = rows.Scan(&v); err != nil {
			return nil, err
		}
		out = append(out, v)
	}
	return out, rows.Err()
}
func (a *app) facetsQuery(ctx context.Context, q string, args ...any) ([]any, error) {
	rows, err := a.db.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []any{}
	for rows.Next() {
		var v string
		var c int
		if err = rows.Scan(&v, &c); err != nil {
			return nil, err
		}
		out = append(out, map[string]any{"value": v, "count": c})
	}
	return out, rows.Err()
}
func (a *app) jsonRows(ctx context.Context, q string, arg any, names []string) ([]any, error) {
	rows, err := a.db.QueryContext(ctx, q, arg)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []any{}
	for rows.Next() {
		vals := make([]any, len(names))
		ptrs := make([]any, len(names))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err = rows.Scan(ptrs...); err != nil {
			return nil, err
		}
		m := map[string]any{}
		for i, n := range names {
			if b, ok := vals[i].([]byte); ok {
				m[n] = string(b)
			} else {
				m[n] = vals[i]
			}
		}
		out = append(out, m)
	}
	return out, rows.Err()
}
func (a *app) jsonRowsNoArg(ctx context.Context, q string, names []string) ([]any, error) {
	rows, err := a.db.QueryContext(ctx, q)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []any{}
	for rows.Next() {
		vals := make([]any, len(names))
		ptrs := make([]any, len(names))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err = rows.Scan(ptrs...); err != nil {
			return nil, err
		}
		m := map[string]any{}
		for i, n := range names {
			if b, ok := vals[i].([]byte); ok {
				m[n] = string(b)
			} else {
				m[n] = vals[i]
			}
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

func credits() any {
	return map[string]any{"sources": []any{map[string]any{"id": "tmdb", "name": "TMDB", "url": "https://www.themoviedb.org/", "notice": "This product uses the TMDB API but is not endorsed or certified by TMDB."}, map[string]any{"id": "tvdb", "name": "TheTVDB", "url": "https://thetvdb.com/", "notice": "Metadata provided by TheTVDB."}, map[string]any{"id": "tvmaze", "name": "TVmaze", "url": "https://www.tvmaze.com/", "notice": "TVmaze data is used under CC BY-SA."}, map[string]any{"id": "simkl", "name": "Simkl", "url": "https://simkl.com/", "notice": "Premiere and finale schedule evidence provided by Simkl; each Simkl record links to its source page."}}}
}
func bounded(r *http.Request, key string, max int, required bool) (string, error) {
	v := r.URL.Query().Get(key)
	if required && strings.TrimSpace(v) == "" {
		return "", &apiError{400, "missing_" + key, key + " is required."}
	}
	if len([]rune(v)) > max {
		return "", &apiError{400, key + "_too_long", fmt.Sprintf("%s is too long.", key)}
	}
	return v, nil
}
func intParam(r *http.Request, key string, def, min, max int) (int, error) {
	raw := r.URL.Query().Get(key)
	if raw == "" {
		return def, nil
	}
	v, err := strconv.Atoi(raw)
	if err != nil || v < min || v > max {
		return 0, &apiError{400, "invalid_" + key, fmt.Sprintf("%s must be between %d and %d.", key, min, max)}
	}
	return v, nil
}
func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
func envInt(k string, d int) int {
	v, err := strconv.Atoi(os.Getenv(k))
	if err == nil && v > 0 {
		return v
	}
	return d
}
func nullable(v sql.NullString) any {
	if v.Valid && v.String != "" {
		return v.String
	}
	return nil
}
func value(v sql.NullString) string {
	if v.Valid {
		return v.String
	}
	return ""
}
func nullableInt(v sql.NullInt64) any {
	if v.Valid {
		return v.Int64
	}
	return nil
}
func nullableNumber(v int) any {
	if v >= 0 {
		return v
	}
	return nil
}
func nullIfEmpty(v string) any {
	if v == "" {
		return nil
	}
	return v
}
func first(v, d string) string {
	if v != "" {
		return v
	}
	return d
}
func toInt(v string) int       { i, _ := strconv.Atoi(v); return i }
func round3(v float64) float64 { x, _ := strconv.ParseFloat(fmt.Sprintf("%.3f", v), 64); return x }
func oneOf(v string, all ...string) bool {
	for _, x := range all {
		if v == x {
			return true
		}
	}
	return false
}
func escapeLike(v string) string {
	v = strings.ReplaceAll(v, "\\", "\\\\")
	v = strings.ReplaceAll(v, "%", "\\%")
	return strings.ReplaceAll(v, "_", "\\_")
}
func cleanPublicURL(v string) string {
	v = strings.TrimSpace(v)
	if v == "" {
		return ""
	}
	if !strings.HasPrefix(v, "https://") && !strings.HasPrefix(v, "http://") {
		return ""
	}
	if strings.Contains(v, "@") || strings.ContainsAny(v, "?#") {
		return ""
	}
	return strings.TrimRight(v, "/")
}
