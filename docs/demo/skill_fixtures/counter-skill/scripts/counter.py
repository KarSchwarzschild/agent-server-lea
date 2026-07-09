"""SG-7 demo · 字符统计脚本 —— 会被 admin bundle 上传后挂到沙箱执行。"""

from __future__ import annotations


def count_chars(text: str) -> dict:
    """统计输入字符串。返回 length / alpha / digits / spaces 四项。"""
    return {
        "text": text,
        "length": len(text),
        "alpha": sum(1 for c in text if c.isalpha()),
        "digits": sum(1 for c in text if c.isdigit()),
        "spaces": sum(1 for c in text if c.isspace()),
    }


if __name__ == "__main__":
    print(count_chars("hello world"))
