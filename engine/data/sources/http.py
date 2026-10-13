# -*- coding: utf-8 -*-
"""HTTP 取数底座。

═══ 本文件的核心结论（2026-10-11 实测，极其重要）═══

东方财富等站点做的是 **TLS/JA3 指纹级** 的拦截，不是 IP 级封禁：

    同一个 URL：
      curl（命令行）                     → HTTP 000   ← 看起来像"被封 IP"
      Python requests（urllib3 默认指纹） → RemoteDisconnected
      curl_cffi 伪装 Chrome              → HTTP 200  ✅

  → 所以**不需要换 IP、不需要代理池**，只需要用浏览器指纹的客户端。
  → 顺带解释了为什么 akshare（底层 requests）会大面积失效。

其它纪律：
  - 腾讯与新浪返回 GBK，必须转码
  - 每个主机有**最小请求间隔**（限流写在底座里，调用方无法绕过）
  - 失败抛 FetchError，由 registry 统一包成 Result（失败即值）
"""
from __future__ import annotations

import time
from typing import Optional
from urllib.parse import urlparse

# ── HTTP 客户端：优先 curl_cffi（浏览器指纹），退化到 requests ────────
try:
    from curl_cffi import requests as _client
    IMPERSONATE = "chrome"
    HAS_CFFI = True
except ImportError:                                          # pragma: no cover
    import requests as _client
    IMPERSONATE = None
    HAS_CFFI = False

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 10

# ── 限流：每个主机的最小请求间隔（秒）────────────────────────────────
# ★ 实测（2026-10-11）：东方财富不是"永久封禁"，而是**软限流**——
#   打得快就掐连接（curl 报 000 / curl_cffi 报 curl(56)），**降速后自动恢复**。
#   触发顺序：先挡指纹 → 继续打 → 连伪装指纹也掐。
#   所以下面这些间隔不是"防封 IP"，而是"别让服务端把你当爬虫"。
MIN_INTERVAL = {
    "push2.eastmoney.com": 2.5,            # 行情（最敏感，实测最先被掐）
    "push2his.eastmoney.com": 2.0,         # K线
    "datacenter-web.eastmoney.com": 2.5,   # 财务/数据中心
    "np-anotice-stock.eastmoney.com": 3.0, # 公告（返回 567 软限流）
    "qt.gtimg.cn": 1.0,                    # 腾讯（宽松）
    "hq.sinajs.cn": 1.0,                   # 新浪（宽松）
    "cninfo.com.cn": 1.5,                  # 巨潮官方站，客气点
}
DEFAULT_INTERVAL = 2.0
_LAST_CALL: dict[str, float] = {}


class FetchError(RuntimeError):
    """取数失败的**基类**。带 kind 字段，供上层做错误分类。

    ★ 为什么必须分类（来自 TradingAgents 的 coverage_gap 教训）：
      "厂商限流" 和 "标的就是没数据" 必须区分开 ——
      前者要说"我们没取到，可以重试/换源"，
      后者才是有信息量的结论（"这只票确实没分过红"）。
      把两者混为一谈，就等于在用沉默当证据。
    """
    kind = "source_error"          # 默认：源出问题

    def __init__(self, message: str, kind: str | None = None):
        super().__init__(message)
        if kind:
            self.kind = kind


class NotFound(FetchError):
    """代码/标的根本不存在（用户的问题本身有误）。"""
    kind = "not_found"


class NoDataInWindow(FetchError):
    """标的存在，但在所查区间内**确实没有记录**。

    ★ 这是**有信息量的结论**，不是错误 —— 例如"该公司从未分红"。
      上层可以直接把它当作事实告诉用户。
    """
    kind = "no_data_in_window"


class RateLimited(FetchError):
    """被限流/被掐连接 —— **绝不能当成"没数据"**。"""
    kind = "rate_limited"


def _throttle(url: str) -> None:
    host = urlparse(url).netloc
    interval = MIN_INTERVAL.get(host, DEFAULT_INTERVAL)
    last = _LAST_CALL.get(host, 0.0)
    wait = interval - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL[host] = time.time()


def _headers(referer: Optional[str] = None, extra: Optional[dict] = None) -> dict:
    h = {
        "User-Agent": UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    }
    if referer:
        h["Referer"] = referer
    if extra:
        h.update(extra)
    return h


def _classify(exc: Exception | None, status: int | None = None) -> str:
    """按现象判定失败类型。"""
    msg = str(exc or "")
    if status is not None:
        if status in (429, 567, 403):
            return "rate_limited"
        if status >= 500:
            return "source_error"
    low = msg.lower()
    # 东财软限流的三种表现（实测）：curl 56 掐连接 / 连接被重置 / HTTP 000
    if any(k in low for k in ("connection closed abruptly", "remote end closed",
                              "connection aborted", "connection reset",
                              "timed out", "timeout", "curl: (56)", "curl: (52)")):
        return "rate_limited"
    if "ssl" in low or "certificate" in low:
        return "source_error"
    return "source_error"


def _do(method: str, url: str, *, referer=None, timeout=DEFAULT_TIMEOUT,
        extra_headers=None, data=None, encoding=None):
    _throttle(url)
    headers = _headers(referer, extra_headers)
    kwargs = {"headers": headers, "timeout": timeout}
    if IMPERSONATE:
        kwargs["impersonate"] = IMPERSONATE
    try:
        r = _client.request(method, url, data=data, **kwargs)
    except Exception as e:                                   # noqa: BLE001
        kind = _classify(e)
        raise FetchError(f"{type(e).__name__}: {e}", kind) from e
    if r.status_code != 200:
        raise FetchError(f"HTTP {r.status_code}", _classify(None, r.status_code))
    if encoding:
        r.encoding = encoding
    return r


def get_text(url: str, referer=None, timeout=DEFAULT_TIMEOUT,
             encoding=None, extra_headers=None) -> str:
    """取文本。encoding='gbk' 用于腾讯/新浪。"""
    r = _do("GET", url, referer=referer, timeout=timeout,
            extra_headers=extra_headers, encoding=encoding)
    if encoding is None and (r.encoding in (None, "ISO-8859-1")):
        try:
            r.encoding = r.apparent_encoding or "utf-8"
        except Exception:                                    # noqa: BLE001
            r.encoding = "utf-8"
    return r.text


def get_json(url: str, referer=None, timeout=DEFAULT_TIMEOUT, extra_headers=None):
    """取 JSON。东财接口全部用它。"""
    r = _do("GET", url, referer=referer, timeout=timeout, extra_headers=extra_headers)
    try:
        return r.json()
    except Exception as e:                                   # noqa: BLE001
        raise FetchError(f"返回不是合法 JSON：{r.text[:120]}") from e


def post_json(url: str, data: dict, referer=None, timeout=DEFAULT_TIMEOUT):
    r = _do("POST", url, referer=referer, timeout=timeout, data=data,
            extra_headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        return r.json()
    except Exception as e:                                   # noqa: BLE001
        raise FetchError(f"返回不是合法 JSON：{r.text[:120]}") from e


def get_jsonp(url: str, referer=None, timeout=DEFAULT_TIMEOUT):
    """取 JSONP 并剥掉回调包装（东财资讯搜索接口用的就是这种格式）。

    返回形如 `jQuery({"code":0,...})`，直接 r.json() 会失败 —— 实测踩到。
    """
    import json as _json

    r = _do("GET", url, referer=referer, timeout=timeout)
    txt = r.text.strip()
    i, j = txt.find("("), txt.rfind(")")
    if i < 0 or j <= i:
        raise FetchError(f"不是合法 JSONP：{txt[:120]}")
    try:
        return _json.loads(txt[i + 1:j])
    except Exception as e:                                   # noqa: BLE001
        raise FetchError(f"JSONP 内容解析失败：{txt[i + 1:i + 120]}") from e


def client_info() -> dict:
    return {
        "客户端": "curl_cffi(浏览器指纹)" if HAS_CFFI else "requests(默认指纹)",
        "impersonate": IMPERSONATE or "-",
        "限流表": MIN_INTERVAL,
    }
