# 代码风格与规范指南 (.agent/rules/code-style.md)

1. 命名规范
变量与方法：一律采用小驼峰命名法（camelCase）或下划线命名法（snake_case），视语言标准而定。

业务清晰度：严禁使用单个字母（如 a, b, t）作为业务核心变量名，必须清晰可读（如 expiredTime, orderStatus）。

2. 异常处理
严禁吞掉异常（禁止空的 catch 块）。

所有的异常捕获必须打印堆栈信息，或转化为统一的业务错误响应（包含 ErrorCode 和 ErrorMessage）。