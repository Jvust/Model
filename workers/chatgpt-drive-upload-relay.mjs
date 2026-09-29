const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file";
const DEFAULT_CHUNK_BYTES = 256 * 1024 * 1024;
const SESSION_ENDPOINT = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      return json({ ok: true, service: "chatgpt-drive-upload-relay" });
    }

    if (url.pathname === "/v1/upload/from-url" && request.method === "POST") {
      return handleUploadFromUrl(request, env);
    }

    return new Response("Not Found", { status: 404 });
  }
};

async function handleUploadFromUrl(request, env) {
  if (!authorizeRelay(request, env)) {
    return json({ error: "unauthorized" }, 401);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  const sourceUrl = normalizeHttpsUrl(body?.source_url);
  const fileName = sanitizeFileName(body?.file_name || "upload.bin");
  const mimeType = String(body?.mime_type || "application/octet-stream");
  const parentId = normalizeDriveId(body?.parent_id || "");
  const chunkBytes = normalizeChunkBytes(body?.chunk_bytes);

  if (!sourceUrl) {
    return json({ error: "invalid_source_url" }, 400);
  }

  if (!sourceHostAllowed(sourceUrl, env)) {
    return json({ error: "source_host_not_allowed" }, 403);
  }

  const accessToken = await getGoogleAccessToken(env);
  const sourceMeta = await probeSource(sourceUrl);

  if (!Number.isFinite(sourceMeta.size) || sourceMeta.size <= 0) {
    return json({ error: "source_size_required" }, 400);
  }

  const sessionUrl = await createDriveSession({
    accessToken,
    fileName,
    mimeType,
    parentId,
    totalBytes: sourceMeta.size
  });

  let offset = 0;
  let chunks = 0;

  while (offset < sourceMeta.size) {
    const end = Math.min(offset + chunkBytes, sourceMeta.size) - 1;

    const sourceResponse = await fetch(sourceUrl.toString(), {
      headers: { Range: `bytes=${offset}-${end}` },
      redirect: "follow"
    });

    if (!(sourceResponse.status === 206 || (offset === 0 && end + 1 === sourceMeta.size && sourceResponse.status === 200))) {
      return json({
        error: "source_range_failed",
        status: sourceResponse.status,
        offset,
        end
      }, 502);
    }

    const expectedLength = end - offset + 1;

    const uploadResponse = await fetch(sessionUrl, {
      method: "PUT",
      headers: {
        "Content-Type": mimeType,
        "Content-Length": String(expectedLength),
        "Content-Range": `bytes ${offset}-${end}/${sourceMeta.size}`
      },
      body: sourceResponse.body
    });

    if (uploadResponse.status === 308) {
      const committed = parseCommittedOffset(uploadResponse.headers.get("Range"));
      offset = committed >= 0 ? committed + 1 : end + 1;
      chunks += 1;
      continue;
    }

    if (uploadResponse.ok) {
      const result = await uploadResponse.json();
      return json({
        ok: true,
        file_id: result.id,
        name: result.name || fileName,
        size: sourceMeta.size,
        chunks: chunks + 1,
        drive_url: result.id ? `https://drive.google.com/file/d/${result.id}/view` : null
      });
    }

    const errorText = await safeText(uploadResponse);
    return json({
      error: "drive_upload_failed",
      status: uploadResponse.status,
      offset,
      end,
      detail: errorText.slice(0, 1000)
    }, 502);
  }

  return json({ error: "unexpected_end" }, 500);
}

function authorizeRelay(request, env) {
  const expected = String(env.RELAY_SECRET || "");
  if (!expected) return false;
  const auth = request.headers.get("Authorization") || "";
  return auth === `Bearer ${expected}`;
}

function sourceHostAllowed(url, env) {
  const raw = String(env.SOURCE_HOST_ALLOWLIST || "").trim();
  if (!raw) return false;
  const allowed = raw
    .split(",")
    .map(v => v.trim().toLowerCase())
    .filter(Boolean);
  return allowed.includes(url.hostname.toLowerCase());
}

function normalizeHttpsUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

function sanitizeFileName(value) {
  return String(value || "upload.bin")
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_")
    .slice(0, 240) || "upload.bin";
}

function normalizeDriveId(value) {
  const id = String(value || "").trim();
  return /^[A-Za-z0-9_-]{10,200}$/.test(id) ? id : "";
}

function normalizeChunkBytes(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_CHUNK_BYTES;
  const unit = 256 * 1024;
  const rounded = Math.floor(n / unit) * unit;
  return Math.min(Math.max(rounded, 8 * 1024 * 1024), 512 * 1024 * 1024);
}

async function probeSource(url) {
  let response = await fetch(url.toString(), { method: "HEAD", redirect: "follow" });
  let size = Number(response.headers.get("Content-Length"));

  if (!response.ok || !Number.isFinite(size) || size <= 0) {
    response = await fetch(url.toString(), {
      headers: { Range: "bytes=0-0" },
      redirect: "follow"
    });

    const contentRange = response.headers.get("Content-Range") || "";
    const match = /\/([0-9]+)$/.exec(contentRange);
    size = match ? Number(match[1]) : Number(response.headers.get("Content-Length"));
  }

  return {
    size,
    acceptRanges: (response.headers.get("Accept-Ranges") || "").toLowerCase()
  };
}

async function createDriveSession({ accessToken, fileName, mimeType, parentId, totalBytes }) {
  const metadata = { name: fileName };
  if (parentId) metadata.parents = [parentId];

  const response = await fetch(SESSION_ENDPOINT, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json; charset=utf-8",
      "X-Upload-Content-Type": mimeType,
      "X-Upload-Content-Length": String(totalBytes)
    },
    body: JSON.stringify(metadata)
  });

  if (!response.ok) {
    throw new Error(`Drive resumable session failed: ${response.status} ${await safeText(response)}`);
  }

  const location = response.headers.get("Location");
  if (!location) {
    throw new Error("Drive resumable session did not return Location header.");
  }

  return location;
}

async function getGoogleAccessToken(env) {
  let refreshToken = String(env.GOOGLE_REFRESH_TOKEN || "");

  if (!refreshToken && env.OAUTH_KV) {
    refreshToken = String((await env.OAUTH_KV.get("refresh_token")) || "");
  }

  if (!refreshToken) {
    throw new Error("Missing Google refresh token.");
  }

  const body = new URLSearchParams();
  body.set("client_id", env.GOOGLE_CLIENT_ID);
  body.set("client_secret", env.GOOGLE_CLIENT_SECRET);
  body.set("refresh_token", refreshToken);
  body.set("grant_type", "refresh_token");

  const response = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body
  });

  const data = await response.json();
  if (!response.ok || !data.access_token) {
    throw new Error(`Google token refresh failed: ${response.status}`);
  }

  return data.access_token;
}

function parseCommittedOffset(rangeHeader) {
  if (!rangeHeader) return -1;
  const match = /bytes=0-([0-9]+)/i.exec(rangeHeader);
  return match ? Number(match[1]) : -1;
}

async function safeText(response) {
  try {
    return await response.text();
  } catch {
    return "";
  }
}

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store"
    }
  });
}
