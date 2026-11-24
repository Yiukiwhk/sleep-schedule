/* ==========================================================================
   桌面版本机令牌
   - 桌面壳启动的 URL 形如 http://127.0.0.1:<port>/?t=<token>
   - 页面只读一次 query 里的 t，存进 sessionStorage，再用 history.replaceState
     把地址栏清干净：令牌不留在地址栏、不进 Referer、不落 localStorage
   - 之后每个 /api/* 请求都带 X-Local-Token（见 shared/api.js）
   - 服务端模式没有令牌，captureLocalToken() 什么都不做，localToken() 返回空串
   ========================================================================== */

const STORAGE_KEY = "ssp.local-token";

/** 从地址栏取一次性令牌并清掉 query；返回本轮拿到的令牌（没有则空串） */
export function captureLocalToken() {
  const url = new URL(window.location.href);
  const token = url.searchParams.get("t");
  if (!token) return "";
  try {
    sessionStorage.setItem(STORAGE_KEY, token);
  } catch (err) {
    /* 隐私模式禁用 sessionStorage：本次会话仍可请求，刷新才会丢 */
  }
  url.searchParams.delete("t");
  const clean = `${url.pathname}${url.search}${url.hash}`;
  try {
    window.history.replaceState(window.history.state, "", clean);
  } catch (err) {
    /* file:// 之类不支持 replaceState 的场景，忽略即可 */
  }
  return token;
}

/** 当前会话的令牌；服务端模式恒为空串 */
export function localToken() {
  try {
    return sessionStorage.getItem(STORAGE_KEY) || "";
  } catch (err) {
    return "";
  }
}
