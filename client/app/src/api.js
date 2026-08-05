// The only module that knows the wire format.
//
// Two rules hold everywhere below, and both come from decisions recorded in
// ROADMAP.md rather than from taste:
//
//   * a 401 is never handled by signing back in. Re-authenticating needs the
//     password, because the password is what unwraps the master key -- there
//     is nothing in the browser to retry with. A silent retry would either
//     fail confusingly or mean the password was being kept somewhere.
//   * an integrity failure is never retried, never downgraded, and never
//     partially rendered. It means somebody changed MongoDB or the Discord
//     side without the key. Retrying it is how a tampered file eventually
//     gets served.

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class ApiError extends Error {
  constructor(status, body, fallback) {
    super((body && body.error) || fallback || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body || {};
    this.code = (body && body.code) || "";
  }

  get isUnauthorized() {
    return this.status === 401;
  }

  get isConflict() {
    return this.status === 409;
  }

  // Distinguished by a code the server sets, not by matching on the message.
  // Telling a tampered file apart from a transient 500 by reading prose is how
  // a retry loop ends up hammering the one thing that must not be retried.
  get isIntegrity() {
    return this.code === "integrity_failure";
  }

  get isRateLimited() {
    return this.status === 429 || this.status === 503;
  }

  get retryAfter() {
    return Number(this.body.retry_after || 0) || 0;
  }
}

// Held in memory only. It is deliberately not in localStorage: the session
// cookie is HttpOnly precisely so a script that gets into the page cannot read
// it, and a CSRF token parked in storage that outlives the tab would hand back
// half of what that buys.
let csrfToken = "";

export function setCsrfToken(token) {
  csrfToken = token || "";
}

function query(params) {
  const usable = Object.entries(params || {}).filter(
    ([, v]) => v !== undefined && v !== null && v !== "",
  );
  if (!usable.length) return "";
  return "?" + new URLSearchParams(usable).toString();
}

async function parse(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    // The server answers JSON for everything under /api. Anything else here
    // is a proxy or a captive portal in the way, and saying so beats a
    // "unexpected token <" that points at this file.
    return { error: "the server did not answer with JSON", raw: text };
  }
}

async function request(method, path, { body, params, signal } = {}) {
  const headers = {};
  if (MUTATING.has(method)) headers["X-CSRF-Token"] = csrfToken;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const response = await fetch(path + query(params), {
    method,
    headers,
    signal,
    body: body === undefined ? undefined : JSON.stringify(body),
    // The cookie is the credential. Same-origin is the default, but stating it
    // means a future change to where this bundle is served fails loudly rather
    // than sending unauthenticated requests that look like a login bug.
    credentials: "same-origin",
    cache: "no-store",
  });

  const payload = await parse(response);
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

// ------------------------------------------------------------------ session

export const health = () => request("GET", "/api/health");

export const session = () => request("GET", "/api/session");

export async function login({ username, password, idleSeconds }) {
  const payload = await request("POST", "/api/login", {
    body: {
      username,
      password,
      // Only ever sent when the user picked something shorter than the
      // ceiling. The server clamps anything longer, so asking for more would
      // be a request that silently does nothing.
      ...(idleSeconds ? { idle_seconds: idleSeconds } : {}),
    },
  });
  setCsrfToken(payload.csrf_token);
  return payload;
}

export async function logout() {
  try {
    return await request("POST", "/api/logout");
  } finally {
    setCsrfToken("");
  }
}

export const revokeOtherSessions = () =>
  request("POST", "/api/sessions/revoke-others");

// -------------------------------------------------------------------- files

export const listDir = (path) => request("GET", "/api/files", { params: { path } });

export const stat = (path) => request("GET", "/api/stat", { params: { path } });

export const search = (q, limit) =>
  request("GET", "/api/search", { params: { q, limit } });

export const makeDir = (path) => request("POST", "/api/dir", { body: { path } });

export const removeFile = (path) =>
  request("DELETE", "/api/file", { params: { path } });

export const removeDir = (path, { recursive = false } = {}) =>
  request("DELETE", "/api/dir", {
    params: { path, recursive: recursive ? "true" : "" },
  });

export const rename = (from, to) =>
  request("POST", "/api/rename", { body: { from, to } });

// -------------------------------------------------------------------- trash

export const listTrash = () => request("GET", "/api/trash");

export const restoreTrash = (id, onConflict) =>
  request("POST", "/api/trash/restore", {
    // Omitted rather than sent as "fail": the server's default is to refuse,
    // and the client only names a strategy once a human has chosen one in
    // response to a 409.
    body: { id, ...(onConflict ? { on_conflict: onConflict } : {}) },
  });

export const purgeTrash = (id) =>
  request("DELETE", "/api/trash", { params: { id } });

export const emptyTrash = () => request("POST", "/api/trash/empty");

// ---------------------------------------------------------------- transfers

export function downloadUrl(path) {
  return "/api/file" + query({ path });
}

/**
 * Upload one file, reporting bytes as the browser sends them.
 *
 * XMLHttpRequest rather than fetch because fetch still has no upload progress
 * event. What this reports is bytes *sent by the browser*, not chunks that
 * reached Discord -- `PUT /api/file` is a whole-file upload and the server
 * does the 9 MiB splitting itself. The prototype drew a "chunk 45 of 217"
 * counter; there is no honest way for the browser to know that number, so it
 * is gone rather than approximated.
 */
export function upload(path, file, { onProgress, signal } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", "/api/file" + query({ path }), true);
    xhr.setRequestHeader("X-CSRF-Token", csrfToken);
    xhr.responseType = "text";
    xhr.withCredentials = false; // same-origin; the cookie rides along anyway

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(event.loaded, event.total);
      }
    };

    const fail = (status, text) => {
      let payload = {};
      try {
        payload = text ? JSON.parse(text) : {};
      } catch {
        payload = {};
      }
      reject(new ApiError(status, payload, "the upload failed"));
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(xhr.responseText ? JSON.parse(xhr.responseText) : {});
        } catch {
          resolve({});
        }
      } else {
        fail(xhr.status, xhr.responseText);
      }
    };
    // A dropped connection mid-upload. The server closes the handle either
    // way, so what was buffered but never committed is released by
    // `_rollback()` on its side; there is nothing for the client to clean up
    // and nothing it could clean up if there were.
    xhr.onerror = () => fail(0, "");
    xhr.ontimeout = () => fail(0, "");
    xhr.onabort = () => reject(new DOMException("aborted", "AbortError"));

    if (signal) {
      if (signal.aborted) {
        xhr.abort();
        return;
      }
      signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }

    xhr.send(file);
  });
}
