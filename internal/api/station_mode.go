package api

import (
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
)

// stationBoundary leaves the account/UI shell available without exposing any
// legacy inference, model installation, YouTube or external LLM entry points.
func stationBoundary() gin.HandlerFunc {
	return func(c *gin.Context) {
		if !stationRequestAllowed(c.Request.Method, c.Request.URL.Path) {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{
				"error": "This station processes meetings through /api/meeting-worker; legacy inference routes are disabled",
			})
			return
		}
		c.Next()
	}
}

func stationRequestAllowed(method, path string) bool {
	// Remaining non-API GETs are locally embedded static files and the React SPA.
	if (method == http.MethodGet || method == http.MethodHead) &&
		!strings.HasPrefix(path, "/api") && !strings.HasPrefix(path, "/install") {
		return true
	}
	switch method + " " + path {
	case "GET /api/v1/auth/registration-status",
		"POST /api/v1/auth/register", "POST /api/v1/auth/login",
		"POST /api/v1/auth/refresh", "POST /api/v1/auth/logout",
		"POST /api/v1/auth/change-password", "POST /api/v1/auth/change-username",
		"GET /api/v1/user/settings", "PUT /api/v1/user/settings",
		"GET /api/v1/events/":
		return true
	default:
		return false
	}
}
