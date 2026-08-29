package main

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	_ "modernc.org/sqlite"
)

func testApp(t *testing.T) *app {
	t.Helper()
	_, source, _, _ := runtime.Caller(0)
	database := filepath.Join(filepath.Dir(source), "..", "..", "seed", "catalog.sqlite3")
	db, err := sql.Open("sqlite", "file:"+database+"?mode=ro&_pragma=query_only(1)&_pragma=foreign_keys(1)")
	if err != nil {
		t.Fatal(err)
	}
	db.SetMaxIdleConns(0)
	t.Cleanup(func() { _ = db.Close() })
	return &app{db: db, databasePath: database, siteName: "Test", maxDays: 366, maxPageSize: 200, static: http.NotFoundHandler()}
}

func request(t *testing.T, a *app, method, target string) (int, http.Header, map[string]any) {
	t.Helper()
	recorder := httptest.NewRecorder()
	a.ServeHTTP(recorder, httptest.NewRequest(method, target, nil))
	result := recorder.Result()
	defer result.Body.Close()
	payload := map[string]any{}
	if method != http.MethodHead {
		if err := json.NewDecoder(result.Body).Decode(&payload); err != nil {
			t.Fatalf("decode %s: %v", target, err)
		}
	}
	return result.StatusCode, result.Header, payload
}

func TestStableRoutesAndHead(t *testing.T) {
	a := testApp(t)
	routes := []string{"/api/v1/health", "/api/v1/meta", "/api/v1/status", "/api/v1/stats", "/api/v1/coverage", "/api/v1/filters?from=2026-08-13&to=2026-08-13", "/api/v1/events?from=2026-08-13&to=2026-08-13", "/api/v1/search?q=Hit%20Point", "/api/v1/calendar?month=2026-08", "/api/v1/credits"}
	for _, target := range routes {
		t.Run(target, func(t *testing.T) {
			status, _, payload := request(t, a, http.MethodGet, target)
			if status != 200 {
				t.Fatalf("status=%d payload=%v", status, payload)
			}
			status, header, _ := request(t, a, http.MethodHead, target)
			if status != 200 {
				t.Fatalf("HEAD status=%d", status)
			}
			if header.Get("Content-Length") == "" {
				t.Fatal("HEAD lacks content length")
			}
		})
	}
}

func TestPublicAPIIsReadOnly(t *testing.T) {
	a := testApp(t)
	for _, method := range []string{"POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"} {
		status, header, payload := request(t, a, method, "/api/v1/events")
		if status != 405 {
			t.Fatalf("%s status=%d", method, status)
		}
		if header.Get("Allow") != "GET, HEAD" {
			t.Fatalf("%s allow=%q", method, header.Get("Allow"))
		}
		if payload["error"].(map[string]any)["code"] != "method_not_allowed" {
			t.Fatalf("%s payload=%v", method, payload)
		}
	}
}

func TestEventsFiltersPaginationAndEvidence(t *testing.T) {
	a := testApp(t)
	status, _, payload := request(t, a, "GET", "/api/v1/events?from=2026-08-13&to=2026-08-13&limit=1&sort=title_asc")
	if status != 200 {
		t.Fatal(payload)
	}
	items := payload["items"].([]any)
	if len(items) != 1 {
		t.Fatalf("items=%d", len(items))
	}
	item := items[0].(map[string]any)
	if len(item["evidence"].([]any)) == 0 {
		t.Fatal("event lost evidence")
	}
	country := item["countries"].([]any)[0].(string)
	status, _, filtered := request(t, a, "GET", "/api/v1/events?from=2026-08-13&to=2026-08-13&country="+country)
	if status != 200 || filtered["pagination"].(map[string]any)["total"].(float64) < 1 {
		t.Fatalf("country filter failed: %v", filtered)
	}
}

func TestInputBoundsAndErrors(t *testing.T) {
	a := testApp(t)
	cases := map[string]string{"/api/v1/events?from=2026-02-30&to=2026-03-01": "invalid_from", "/api/v1/events?from=2026-01-01&to=2028-01-01": "date_range_too_large", "/api/v1/events?country=USA": "invalid_country", "/api/v1/events?limit=10000": "invalid_limit", "/api/v1/search?q=": "missing_q"}
	for target, code := range cases {
		status, _, payload := request(t, a, "GET", target)
		if status != 400 {
			t.Fatalf("%s status=%d", target, status)
		}
		got := payload["error"].(map[string]any)["code"]
		if got != code {
			t.Fatalf("%s code=%v want=%s", target, got, code)
		}
	}
}

func TestFrontendCountryAndResetUseRouterState(t *testing.T) {
	_, source, _, _ := runtime.Caller(0)
	root := filepath.Join(filepath.Dir(source), "..", "..")
	body, err := os.ReadFile(filepath.Join(root, "frontend", "src", "App.tsx"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(body)
	for _, want := range []string{`value={search.country}`, `onChange={(country) => setSearch({ country })}`, `navigate({ search: { view: 'day', date: today } })`} {
		if !strings.Contains(text, want) {
			t.Fatalf("missing deterministic state contract %q", want)
		}
	}
}
