"""断点续爬状态管理。

状态文件：<output>/.crawl_state.json
结构：
{
  "seed_url": "https://example.com",
  "visited":  { "https://example.com": "ok" | "fail:reason" },
  "queue":    [["https://example.com/docs/", 1], ...],   // [url, depth]
  "external": ["https://github.com/...", ...]
}

每爬完一页（成功或失败）调用 save() 持久化状态。
"""
from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_FILENAME = ".crawl_state.json"


class CrawlSession:
    """断点续爬状态对象。

    Attributes:
        seed_url:  起始 URL。
        visited:   已处理 URL → 状态字符串（"ok" 或 "fail:reason"）。
        queue:     待爬队列，元素为 (url, depth)。
        external:  遇到的跨域链接（仅记录）。
        state_path: 状态文件路径。
    """

    def __init__(self, output_dir: Path, seed_url: str) -> None:
        self.seed_url = seed_url
        self.state_path = output_dir / _STATE_FILENAME
        self.visited: dict[str, str] = {}
        self.queue: deque[tuple[str, int]] = deque()
        self.external: list[str] = []

    # ──────────────────────────────────────────
    # 持久化
    # ──────────────────────────────────────────

    def load(self) -> bool:
        """从磁盘加载状态。返回 True 表示成功恢复断点。"""
        if not self.state_path.exists():
            return False
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if data.get("seed_url") != self.seed_url:
                logger.warning(
                    "状态文件的 seed_url (%s) 与当前不符，忽略断点",
                    data.get("seed_url"),
                )
                return False
            self.visited = data.get("visited", {})
            self.queue = deque(
                (item[0], item[1]) for item in data.get("queue", [])
            )
            self.external = data.get("external", [])
            logger.info(
                "恢复断点：已爬 %d 页，待爬 %d 页",
                len(self.visited),
                len(self.queue),
            )
            return True
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("状态文件读取失败（%s），将从头开始", exc)
            return False

    def save(self) -> None:
        """将当前状态写入磁盘。"""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "seed_url": self.seed_url,
            "visited": self.visited,
            "queue": [[url, depth] for url, depth in self.queue],
            "external": self.external,
        }
        self.state_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ──────────────────────────────────────────
    # 队列操作
    # ──────────────────────────────────────────

    def enqueue(self, url: str, depth: int) -> None:
        """将 URL 加入待爬队列（自动去重）。"""
        if url not in self.visited and not self._in_queue(url):
            self.queue.append((url, depth))

    def _in_queue(self, url: str) -> bool:
        return any(u == url for u, _ in self.queue)

    def dequeue(self) -> tuple[str, int]:
        """从队头取出下一个待爬项。"""
        return self.queue.popleft()

    def mark_ok(self, url: str) -> None:
        self.visited[url] = "ok"

    def mark_fail(self, url: str, reason: str) -> None:
        self.visited[url] = f"fail:{reason}"

    def add_external(self, urls: list[str]) -> None:
        existing = set(self.external)
        for u in urls:
            if u not in existing:
                self.external.append(u)
                existing.add(u)

    # ──────────────────────────────────────────
    # 状态查询
    # ──────────────────────────────────────────

    def is_visited(self, url: str) -> bool:
        return url in self.visited

    def total_visited(self) -> int:
        return len(self.visited)

    def ok_pages(self) -> list[str]:
        return [u for u, s in self.visited.items() if s == "ok"]

    def failed_pages(self) -> list[tuple[str, str]]:
        return [
            (u, s.removeprefix("fail:"))
            for u, s in self.visited.items()
            if s.startswith("fail:")
        ]

    def clear(self, output_dir: Path) -> None:
        """清除状态（force 模式下调用）。"""
        self.visited.clear()
        self.queue.clear()
        self.external.clear()
        if self.state_path.exists():
            self.state_path.unlink()
