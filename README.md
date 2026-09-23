# 分布式任务工作流DAG编排与执行引擎

基于Vue 3 + FastAPI的任务编排平台，DAG拓扑排序、任务状态机、多Worker并发池、执行甘特图。

## 目标用户
数据工程师、ETL/ML Pipeline开发者、技术架构师

## 技术栈
- 前端: Vue 3 + TypeScript + Vite + Pinia + Element Plus + ECharts
- 后端: Python FastAPI + NumPy + SQLite + WebSocket

## 核心功能
1. DAG工作流编辑器：拖拽添加任务节点、连线建立依赖关系、BFS拓扑排序验证环检测
2. Spring StateMachine风格任务状态机：PENDING→RUNNING→SUCCESS/FAILED/TIMEOUT
3. 多Worker并发池模拟：可配置Worker数量、任务执行耗时模拟(指数分布)
4. 任务编排策略：FIFO/优先级/最大并发三种调度策略
5. 重试机制：可配置最大重试次数、指数退避延迟
6. 执行监控：ECharts甘特图时间线渲染、实时WebSocket推送任务状态
7. 熔断保护：连续失败阈值触发熔断，冷却时间后自动恢复
8. 执行结果导出：将选定的一次或多次执行整理为 JSON 文件下载

## 执行结果导出

- 在「执行历史」中勾选一次或多次执行（也可直接导出当前执行），导出前可选择要包含的环节（工作流定义 / 任务执行结果 / 执行日志 / 熔断器状态）与时间范围，面板实时预览将包含的数据量。
- 「导出记录」展示每次导出的范围、文件大小与结果（成功 / 失败 / 无数据），可下载、查看明细或对失败的导出重新开始；刷新页面或重启服务后记录仍在。

设计要点：

- **幂等**：相同范围（执行集合 + 环节 + 时间范围，顺序无关）经哈希得到固定文件名，重复导出复用同一记录与文件，不产生重复文件。
- **原子性**：文件先写入 `.tmp` 再 `os.replace` 落盘；导出失败或被中断只清理临时文件，不留下半个文件；服务重启时将 PROCESSING 记录置为失败并清理残留临时文件，可一键重新开始。
- **空结果不落盘**：范围内没有符合条件的数据时记录标记为「无数据」，不生成任何文件（下载按钮不可用）。
- **口径一致**：预览、导出文件内容与「导出明细」页面均由后端同一个 `build_content` 构建，明细页直接解析已落盘文件，数字与记录行、下载文件完全一致。
- **持久化**：执行记录与导出记录存于 `backend/data/engine.db`，导出文件存于 `backend/data/exports/`。

## 启动

```bash
# 后端（FastAPI，端口 8000，SQLite 自动初始化于 backend/data/）
cd backend && python3 -m uvicorn app.main:app --reload

# 前端（Vite，端口 3000，已配置 /api 与 /ws 代理）
cd frontend && npm install && npm run dev
```
