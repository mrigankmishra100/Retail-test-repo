export function resolveApiBase(moduleUrl, override) {
  // Retain the existing explicit build-time override. Otherwise resolve from
  // the module URL, not the hostname root or a page's optional query/hash.
  return override || new URL("../api/", moduleUrl).href;
}
