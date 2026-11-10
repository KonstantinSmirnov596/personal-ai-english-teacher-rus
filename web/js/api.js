// HTTP helpers for /api. Same-origin requests carry the session cookie and an Origin header.

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message || code);
    this.status = status;
    this.code = code;
  }
}

export async function api(method, path, body) {
  const init = { method, headers: {}, credentials: 'same-origin' };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let resp;
  try {
    resp = await fetch(path, init);
  } catch (e) {
    throw new ApiError(0, 'network', String(e));
  }
  if (resp.status === 204) return null;
  let data = null;
  try {
    data = await resp.json();
  } catch {
    data = null;
  }
  if (!resp.ok) {
    const err = (data && data.error) || {};
    throw new ApiError(resp.status, err.code || 'internal', err.message_ru || '');
  }
  return data;
}

export const get = (p) => api('GET', p);
export const post = (p, b) => api('POST', p, b === undefined ? {} : b);
export const patch = (p, b) => api('PATCH', p, b);
export const del = (p) => api('DELETE', p);
