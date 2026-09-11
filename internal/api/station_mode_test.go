package api

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"scriberr/internal/auth"
	"scriberr/internal/config"
)

func TestStationBoundaryBlocksAllLegacyInferenceBeforeHandlers(t *testing.T) {
	// No processing services are initialized: a legacy handler must never run.
	handler := &Handler{config: &config.Config{StationMode: true}}
	router := SetupRoutes(handler, auth.NewAuthService("test-only-local-jwt-secret"))
	for _, request := range []struct{ method, path string }{
		{"POST", "/api/v1/transcription/upload"},
		{"POST", "/api/v1/transcription/youtube"},
		{"POST", "/api/v1/transcription/quick"},
		{"POST", "/api/v1/config/openai/validate"},
		{"GET", "/api/v1/chat/models"},
		{"POST", "/api/v1/summarize/"},
		{"POST", "/api/v1/llm/config"},
		{"GET", "/api/v1/cli/download"},
		{"GET", "/install.sh"},
		{"GET", "/api/v1/admin/queue/stats"},
	} {
		response := httptest.NewRecorder()
		router.ServeHTTP(response, httptest.NewRequest(request.method, request.path, nil))
		if response.Code != http.StatusForbidden {
			t.Errorf("%s %s returned %d; wanted 403", request.method, request.path, response.Code)
		}
	}
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest("GET", "/health", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("station health returned %d", response.Code)
	}
}

func TestStationAllowsAccountAndLocalUIRoutes(t *testing.T) {
	for _, route := range []struct{ method, path string }{
		{"GET", "/meeting-intelligence"}, {"GET", "/assets/app.js"},
		{"POST", "/api/v1/auth/login"}, {"POST", "/api/v1/auth/register"},
		{"GET", "/api/v1/auth/registration-status"}, {"POST", "/api/v1/auth/refresh"},
	} {
		if !stationRequestAllowed(route.method, route.path) {
			t.Errorf("expected %s %s to remain available", route.method, route.path)
		}
	}
}
