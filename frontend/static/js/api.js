/*
 * API çağrı yardımcısı. Oturum httponly cookie ile taşındığı için fetch
 * özel bir Authorization header'ına ihtiyaç duymaz (same-origin istekte
 * cookie otomatik gider). Durum değiştiren istekler (POST/PUT/DELETE)
 * CSRF cookie'sinden okunan token'ı X-CSRF-Token header'ı olarak eklemek
 * ZORUNDADIR - backend bunu doğrular (bkz. security/auth.py).
 */

function getCookie(name) {
  const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : null;
}

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details || {};
  }
}

async function apiFetch(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = Object.assign({}, options.headers || {});

  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.json);
  }

  if (UNSAFE_METHODS.has(method)) {
    const csrfToken = getCookie("jetson_camera_csrf");
    if (csrfToken) {
      headers["X-CSRF-Token"] = csrfToken;
    }
  }

  const response = await fetch(path, {
    method,
    headers,
    body: options.body,
    credentials: "same-origin",
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (err) {
    payload = null;
  }

  if (!response.ok) {
    const code = payload && payload.error ? payload.error.code : "UNKNOWN_ERROR";
    const message = payload && payload.error ? payload.error.message : `HTTP ${response.status}`;
    const details = payload && payload.error ? payload.error.details : {};

    if (code === "UNAUTHORIZED" && !path.startsWith("/api/auth/")) {
      window.location.href = "/login";
    }

    throw new ApiError(response.status, code, message, details);
  }

  return payload ? payload.data : null;
}

const Api = {
  get: (path) => apiFetch(path, { method: "GET" }),
  post: (path, json) => apiFetch(path, { method: "POST", json }),
  put: (path, json) => apiFetch(path, { method: "PUT", json }),
  del: (path) => apiFetch(path, { method: "DELETE" }),
  ApiError,
};
