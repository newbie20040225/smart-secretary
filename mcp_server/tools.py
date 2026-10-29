"""本地 MCP 工具的真实实现。

原项目（mcp_server/mcp_tools.py）里的工具只有 my_search / add / multiply，
且 my_search 强依赖智谱密钥。本文件把「智能小秘书」需求里的能力补齐，
并保证**不依赖任何外部密钥也能跑通**：

| 需求（见 40分钟学会MCP+Agent开发智能小秘书.md） | 对应工具                |
|----------------------------------------------|------------------------|
| 获取网络上公开的数据                          | my_search              |
| 解析和提取网页内容                            | fetch_webpage          |
| 自动分析数据并生成分析图表                    | analyze_and_chart      |
| 查询火车票                                    | query_train_ticket     |
| 规划出行路线                                  | plan_route             |
| 减肥菜谱 / 生活建议                           | get_weather + 模型自身 |
"""
from __future__ import annotations

import ast
import csv
import io
import json
import operator
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from env_utils import OUTPUT_DIR, TAVILY_API_KEY, ZHIPU_API_KEY

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


# ======================================================================
# 1. 基础计算（与原项目 add / multiply 保持完全一致）
# ======================================================================
def add(a: int, b: int) -> int:
    """加法运算: 计算两个数字相加"""
    return a + b


def multiply(a: int, b: int) -> int:
    """乘法运算：计算两个数字相乘"""
    return a * b


# ---------- 安全的四则运算求值 ----------
_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("表达式包含不支持的语法，只能做 + - * / // % ** 与括号运算")


def calculator(expression: str) -> str:
    """四则运算计算器。支持 (3 + 5) * 12、2**10、17 % 5 这类表达式。"""
    cleaned = expression.replace("×", "*").replace("÷", "/").replace("X", "*").strip()
    try:
        tree = ast.parse(cleaned, mode="eval")
        value = _eval_node(tree)
        return f"{expression} = {value:g}"
    except Exception as exc:  # noqa: BLE001
        return f"计算失败：{exc}"


# ======================================================================
# 2. 联网搜索
# ======================================================================
def _search_tavily(query: str, max_results: int) -> str:
    resp = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": TAVILY_API_KEY, "query": query, "max_results": max_results},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    items = resp.json().get("results", [])
    if not items:
        return ""
    return "\n\n".join(f"【{i.get('title')}】\n{i.get('url')}\n{i.get('content', '')[:600]}" for i in items)


def _search_zhipu(query: str) -> str:
    from zhipuai import ZhipuAI  # 延迟导入，没装也不影响其它工具

    client = ZhipuAI(api_key=ZHIPU_API_KEY)
    response = client.web_search.web_search(search_engine="search-std", search_query=query)
    if getattr(response, "search_result", None):
        return "\n\n".join(d.content for d in response.search_result)
    return ""


def _search_html(query: str, max_results: int) -> str:
    """无密钥兜底：抓 DuckDuckGo / Bing 的 HTML 结果页做粗略解析。"""
    engines = [
        ("https://html.duckduckgo.com/html/?q=", "result__a", "result__snippet"),
        ("https://cn.bing.com/search?q=", "h2 a", "p"),
    ]
    for base, title_sel, snippet_sel in engines:
        try:
            resp = httpx.get(
                base + quote(query),
                headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                timeout=TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            titles = soup.select(title_sel)[:max_results]
            snippets = soup.select(snippet_sel)
            blocks = []
            for idx, node in enumerate(titles):
                title = node.get_text(" ", strip=True)
                href = node.get("href", "") if node.name == "a" else ""
                snippet = snippets[idx].get_text(" ", strip=True) if idx < len(snippets) else ""
                if title:
                    blocks.append(f"【{title}】\n{href}\n{snippet}")
            if blocks:
                return "\n\n".join(blocks)
        except Exception:  # noqa: BLE001
            continue
    return ""


def my_search(query: str, max_results: int = 5) -> str:
    """搜索互联网上的内容，包括实时资讯、技术文档、金融、生活等公开数据。

    :param query: 需要搜索的内容或关键词
    :param max_results: 期望返回的结果条数（默认 5）
    """
    try:
        if TAVILY_API_KEY:
            text = _search_tavily(query, max_results)
        elif ZHIPU_API_KEY:
            text = _search_zhipu(query)
        else:
            text = _search_html(query, max_results)

        if not text:
            text = _search_html(query, max_results)
        if not text:
            return (
                f"没有搜索到「{query}」的相关内容。"
                "（当前使用无密钥的 HTML 检索兜底，可在 .env 中配置 ZHIPU_API_KEY / TAVILY_API_KEY 提升质量）"
            )
        return f"关于「{query}」的检索结果：\n\n{text}"
    except Exception as exc:  # noqa: BLE001
        return f"搜索失败：{exc}"


# ======================================================================
# 3. 网页内容解析与提取
# ======================================================================
def fetch_webpage(url: str, max_chars: int = 4000) -> str:
    """抓取指定 URL 的网页，抽取标题与正文纯文本。

    :param url: 完整网页地址，例如 https://example.com/article
    :param max_chars: 最多返回的字符数（默认 4000）
    """
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()

        # 让 httpx 按声明或猜测的编码解码，避免中文乱码
        if resp.encoding is None:
            resp.encoding = resp.charset_encoding or "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe"]):
            tag.decompose()

        title = (soup.title.get_text(strip=True) if soup.title else "") or url
        text = re.sub(r"\n{2,}", "\n", soup.get_text("\n", strip=True))
        text = "\n".join(line for line in text.splitlines() if line.strip())

        truncated = text[:max_chars]
        suffix = f"\n\n...(正文共 {len(text)} 字，已截断)" if len(text) > max_chars else ""
        return f"标题：{title}\n来源：{url}\n\n{truncated}{suffix}"
    except Exception as exc:  # noqa: BLE001
        return f"抓取网页失败：{exc}"


# ======================================================================
# 4. 时间
# ======================================================================
def get_current_time(timezone_offset_hours: float = 8) -> str:
    """获取当前日期与时间。

    :param timezone_offset_hours: 时区偏移，默认 8（东八区 / 北京时间）
    """
    tz = timezone(timedelta(hours=timezone_offset_hours))
    now = datetime.now(tz)
    weekday = "一二三四五六日"[now.weekday()]
    return now.strftime(f"%Y-%m-%d %H:%M:%S（星期{weekday}，UTC%z）")


# ======================================================================
# 5. 天气（open-meteo，无需密钥）
# ======================================================================
_WMO_CODE = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "小雨", 55: "中雨", 56: "冻毛毛雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "强阵雨", 82: "暴雨", 85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴伴冰雹",
}


def get_weather(city: str) -> str:
    """查询指定城市的实时天气与未来三天预报（数据源 open-meteo，免密钥）。

    :param city: 城市名，中文或英文均可，例如「上海」「Tokyo」
    """
    try:
        geo = httpx.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "zh", "format": "json"},
            timeout=TIMEOUT,
        ).json()
        results = geo.get("results") or []
        if not results:
            return f"没有找到城市「{city}」，请换一个名称试试。"
        place = results[0]
        lat, lon = place["latitude"], place["longitude"]

        forecast = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "forecast_days": 3,
                "timezone": "auto",
            },
            timeout=TIMEOUT,
        ).json()

        cur = forecast["current"]
        lines = [
            f"【{place.get('name')} {place.get('country', '')}】",
            f"当前：{_WMO_CODE.get(cur['weather_code'], '未知')}  "
            f"{cur['temperature_2m']}℃  湿度 {cur['relative_humidity_2m']}%  "
            f"风速 {cur['wind_speed_10m']} km/h",
            "",
            "未来三天：",
        ]
        daily = forecast["daily"]
        for date, code, tmax, tmin, pop in zip(
            daily["time"], daily["weather_code"],
            daily["temperature_2m_max"], daily["temperature_2m_min"],
            daily["precipitation_probability_max"],
        ):
            lines.append(
                f"{date}  {_WMO_CODE.get(code, '未知')}  {tmin}~{tmax}℃  降水概率 {pop}%"
            )
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"查询天气失败：{exc}"


# ======================================================================
# 6. 12306 火车票余票查询
# ======================================================================
_STATION_JS = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
_station_cache: dict[str, str] = {}


def _load_stations() -> dict[str, str]:
    global _station_cache
    if _station_cache:
        return _station_cache
    resp = httpx.get(_STATION_JS, headers={"User-Agent": UA}, timeout=TIMEOUT)
    resp.encoding = "utf-8"
    mapping: dict[str, str] = {}
    for chunk in resp.text.split("@"):
        parts = chunk.split("|")
        if len(parts) > 3:
            # 站名 -> 电报码
            mapping[parts[1]] = parts[2]
            mapping[parts[1] + "市"] = parts[2]
    _station_cache = mapping
    return mapping


def query_train_ticket(from_station: str, to_station: str, travel_date: str) -> str:
    """查询 12306 指定日期、指定区间的火车/高铁余票。

    :param from_station: 出发站中文名，例如「北京南」
    :param to_station: 到达站中文名，例如「上海虹桥」
    :param travel_date: 出发日期，格式 YYYY-MM-DD
    """
    try:
        stations = _load_stations()
        f_code = stations.get(from_station) or stations.get(from_station + "市")
        t_code = stations.get(to_station) or stations.get(to_station + "市")
        if not f_code or not t_code:
            return (f"车站电报码未识别：出发「{from_station}」/ 到达「{to_station}」。"
                    "请确认站点名称，例如「北京南」「上海虹桥」。")

        # 12306 需要先访问 init 页拿 Cookie，否则直接请求会被 302 挡掉
        with httpx.Client(headers={"User-Agent": UA}, timeout=TIMEOUT, follow_redirects=True) as client:
            client.get("https://kyfw.12306.cn/otn/leftTicket/init",
                       headers={"Referer": "https://kyfw.12306.cn/otn/leftTicket/init"})

            # 查询入口会动态返回当前可用的查询地址（如 leftTicket/queryG）
            probe = client.get(
                "https://kyfw.12306.cn/otn/leftTicket/query",
                params={
                    "leftTicketDTO.train_date": travel_date,
                    "leftTicketDTO.from_station": f_code,
                    "leftTicketDTO.to_station": t_code,
                    "purpose_codes": "ADULT",
                },
                headers={"Referer": "https://kyfw.12306.cn/otn/leftTicket/init"},
            )
            candidate = "leftTicket/queryG"
            try:
                c_url = probe.json().get("c_url")
                if c_url:
                    candidate = c_url
            except Exception:  # noqa: BLE001
                pass

            data = None
            for path in [candidate, "leftTicket/queryG", "leftTicket/queryZ", "leftTicket/queryA"]:
                resp = client.get(
                    f"https://kyfw.12306.cn/otn/{path}",
                    params={
                        "leftTicketDTO.train_date": travel_date,
                        "leftTicketDTO.from_station": f_code,
                        "leftTicketDTO.to_station": t_code,
                        "purpose_codes": "ADULT",
                    },
                    headers={"Referer": "https://kyfw.12306.cn/otn/leftTicket/init"},
                )
                try:
                    payload = resp.json()
                except Exception:  # noqa: BLE001
                    continue
                if payload.get("data", {}).get("result"):
                    data = payload["data"]
                    break
            if not data:
                return (f"{travel_date} {from_station} → {to_station} 没有查询到车次。"
                        "（可能是未到起售期、已过发车日，或 12306 临时限制了访问）")

        rows = data.get("result") or []
        station_map = data.get("map") or {}

        lines = [f"{travel_date} {from_station} → {to_station}（共 {len(rows)} 个车次，展示前 15 个）",
                 "车次     出发→到达      历时     二等座  一等座  商务座  无座"]
        for raw in rows[:15]:
            f = raw.split("|")
            if len(f) < 33:
                continue
            dep = station_map.get(f[6], f[4])
            arr = station_map.get(f[7], f[5])
            lines.append(
                f"{f[3]:<8} {f[8]}→{f[9]}  {f[10]:<7} "
                f"{(f[30] or '--'):<6} {(f[31] or '--'):<6} {(f[32] or '--'):<6} {f[26] or '--'}"
            )
        lines.append("\n（余票信息实时变动，请以 12306 官方为准）")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"查询火车票失败（12306 接口可能临时限流，请稍后重试）：{exc}"


# ======================================================================
# 7. 出行路线规划（Nominatim 地理编码 + OSRM 路径，均免密钥）
# ======================================================================
def _geocode(name: str) -> Optional[tuple]:
    """地名 → (lon, lat, 显示名)。优先 open-meteo（稳定、免密钥），失败再退到 Nominatim。"""
    try:
        r = httpx.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": name, "count": 1, "language": "zh", "format": "json"},
            timeout=TIMEOUT,
        ).json()
        if r.get("results"):
            p = r["results"][0]
            label = " ".join(x for x in [p.get("name"), p.get("admin1"), p.get("country")] if x)
            return float(p["longitude"]), float(p["latitude"]), label or name
    except Exception:  # noqa: BLE001
        pass

    try:
        r = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": name, "format": "json", "limit": 1, "accept-language": "zh-CN"},
            headers={"User-Agent": "smart-secretary-demo/1.0"},
            timeout=TIMEOUT,
        ).json()
        if r:
            return float(r[0]["lon"]), float(r[0]["lat"]), r[0].get("display_name", name)
    except Exception:  # noqa: BLE001
        pass
    return None


def plan_route(origin: str, destination: str, mode: str = "driving") -> str:
    """规划两地之间的出行路线与距离/耗时估算。

    :param origin: 起点，中文地址或地名，例如「杭州」
    :param destination: 终点，例如「上海」
    :param mode: 出行方式，可选 driving / walking / cycling
    """
    profile = {"driving": "driving", "walking": "foot", "cycling": "bike"}.get(mode, "driving")
    mode_cn = {"driving": "驾车", "walking": "步行", "cycling": "骑行"}.get(mode, mode)

    try:
        a, b = _geocode(origin), _geocode(destination)
        if not a or not b:
            return f"地点解析失败：无法定位「{origin if not a else destination}」"

        route = None
        endpoints = [
            "https://router.project-osrm.org/route/v1",
            "https://routing.openstreetmap.de/routed-car/route/v1",
        ]
        for base in endpoints:
            try:
                resp = httpx.get(
                    f"{base}/{profile}/{a[0]},{a[1]};{b[0]},{b[1]}",
                    params={"overview": "false", "steps": "true"},
                    headers={"User-Agent": UA},
                    timeout=TIMEOUT,
                ).json()
                if resp.get("code") == "Ok" and resp.get("routes"):
                    route = resp["routes"][0]
                    break
            except Exception:  # noqa: BLE001
                continue

        if route is None:
            return "路线服务暂不可用，请稍后重试。"

        km = route["distance"] / 1000
        hours, minutes = divmod(int(route["duration"] / 60), 60)

        lines = [
            f"路线规划（{mode_cn}）",
            f"起点：{a[2]}",
            f"终点：{b[2]}",
            f"总距离：约 {km:.1f} 公里    预计耗时：{hours} 小时 {minutes} 分钟",
            "",
            "分步指引：",
        ]
        idx = 0
        for leg in route.get("legs", []):
            for step in leg.get("steps", [])[:20]:
                idx += 1
                man = step.get("maneuver", {})
                road = step.get("name") or "无名道路"
                lines.append(f"{idx:>2}. {man.get('type', '')} {road}  {step['distance']:.0f} 米")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"路线规划失败：{exc}"


# ======================================================================
# 8. 数据分析 + 生成图表
# ======================================================================
def _parse_table(data: str) -> list[dict]:
    """把 CSV 或 JSON 文本解析成 list[dict]。"""
    data = data.strip()
    if data.startswith("[") or data.startswith("{"):
        obj = json.loads(data)
        if isinstance(obj, dict):
            obj = [obj]
        return [dict(row) for row in obj]

    # 退化处理：尝试常见分隔符
    for delim in [",", "\t", ";", "|"]:
        try:
            reader = csv.DictReader(io.StringIO(data), delimiter=delim)
            rows = [r for r in reader]
            if rows and reader.fieldnames and len(reader.fieldnames) > 1:
                return rows
        except Exception:  # noqa: BLE001
            continue
    raise ValueError("无法解析数据，请传入 CSV（含表头）或 JSON 数组文本")


def analyze_and_chart(
    data: str,
    x_field: str = "",
    y_field: str = "",
    chart_type: str = "bar",
    title: str = "数据分析结果",
) -> str:
    """分析结构化数据并生成统计摘要 + 图表图片。

    :param data: CSV 文本（第一行为表头）或 JSON 数组文本，例如 "月份,销量\\n1月,120\\n2月,150"
    :param x_field: 作为横轴的字段名；留空则用第一列
    :param y_field: 作为纵轴的字段名；留空则用第一个数值列
    :param chart_type: 图表类型，可选 bar / line / pie / scatter
    :param title: 图表标题
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # 无界面后端，服务端必须
        import matplotlib.pyplot as plt

        # 中文字体
        for font in ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]:
            try:
                matplotlib.rcParams["font.sans-serif"] = [font]
                matplotlib.rcParams["axes.unicode_minus"] = False
                break
            except Exception:  # noqa: BLE001
                continue

        rows = _parse_table(data)
        if not rows:
            return "数据为空，无法分析。"

        fields = list(rows[0].keys())
        xf = x_field or fields[0]

        # 自动挑一个数值列做纵轴
        if not y_field:
            for f in fields:
                if f == xf:
                    continue
                try:
                    float(str(rows[0][f]).replace(",", ""))
                    y_field = f
                    break
                except (ValueError, TypeError):
                    continue
        if not y_field:
            return f"没有找到可用的数值列，字段为：{fields}"

        xs = [str(r.get(xf, "")) for r in rows]
        ys = [float(str(r.get(y_field, 0)).replace(",", "")) for r in rows]

        # ---- 统计摘要 ----
        total = sum(ys)
        summary = (
            f"【{title}】\n"
            f"样本数：{len(ys)}    字段：{xf} → {y_field}\n"
            f"总和：{total:,.2f}    均值：{statistics.fmean(ys):,.2f}    "
            f"中位数：{statistics.median(ys):,.2f}\n"
            f"最大：{max(ys):,.2f}（{xs[ys.index(max(ys))]}）    "
            f"最小：{min(ys):,.2f}（{xs[ys.index(min(ys))]}）"
        )
        if len(ys) > 1:
            summary += f"\n标准差：{statistics.pstdev(ys):,.2f}"
            prev = ys[0]
            trend = []
            for v in ys[1:]:
                trend.append("上升" if v > prev else ("下降" if v < prev else "持平"))
                prev = v
            summary += f"\n整体趋势：{' → '.join(trend)}"

        # ---- 出图 ----
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=130)
        if chart_type == "pie":
            ax.pie(ys, labels=xs, autopct="%1.1f%%", startangle=90)
            ax.axis("equal")
        elif chart_type == "line":
            ax.plot(xs, ys, marker="o", linewidth=2)
        elif chart_type == "scatter":
            ax.scatter(xs, ys, s=70)
        else:  # bar
            ax.bar(xs, ys)
            for i, v in enumerate(ys):
                ax.text(i, v, f"{v:,.0f}", ha="center", va="bottom", fontsize=9)

        ax.set_title(title, fontsize=13)
        ax.set_xlabel(xf)
        ax.set_ylabel(y_field)
        ax.grid(alpha=0.3, linestyle="--")
        plt.xticks(rotation=30, ha="right")
        fig.tight_layout()

        filename = f"chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path = OUTPUT_DIR / filename
        fig.savefig(path)
        plt.close(fig)

        return f"{summary}\n\n图表已生成：{path}\n（在界面上以 Markdown 图片路径引用：{path.as_posix()}）"
    except Exception as exc:  # noqa: BLE001
        return f"数据分析失败：{exc}"


__all__ = [
    "add",
    "multiply",
    "calculator",
    "my_search",
    "fetch_webpage",
    "get_current_time",
    "get_weather",
    "query_train_ticket",
    "plan_route",
    "analyze_and_chart",
]
