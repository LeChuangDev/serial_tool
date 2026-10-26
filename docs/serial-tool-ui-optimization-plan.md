# Leeco-Serial 界面与功能优化计划

## Context

工具已完成性能重构（线程化接收、QPlainTextEdit 渲染、多模块拆分）。当前 UI 为"顶部 3 行参数 + 左接收右发送"的平铺布局，参数堆叠无分组，视觉层次弱，缺少主流串口助手（XCOM/SSCOM）的常见能力。

用户确认方向：
- **布局**：左参数面板 + 右上下区（XCOM 风格）
- **功能**：多条发送列表、接收区右键菜单 + Ctrl+滚轮字号、统计面板增强、黑底绿字经典风格
- **视觉**：深蓝工业风精修
- 界面**不使用图片资源**，全部用 QSS 实现

## 布局设计

```
┌────────────────────────────────────────────────────────────┐
│ 菜单栏                                                       │
├───────────────┬────────────────────────────────────────────┤
│ 左参数面板     │  接收区头部（统计 + 迷你速率图 + 清空按钮）    │
│ ┌───────────┐ │  ┌────────────────────────────────────────┐│
│ │串口配置卡片 │ │  │        接收区 RecvPlainTextEdit         ││
│ │ 端口/波特率 │ │  │   （黑底绿字 / 蓝底白字可切换）           ││
│ │ 数据/停止位 │ │  └────────────────────────────────────────┘│
│ │ 校验位     │ │  查找条（Ctrl+F）                           │
│ │[连接][刷新] │ ├────────────────────────────────────────────┤
│ ├───────────┤ │  发送区头部（模式/编码/新行/回显/CRC16）       │
│ │显示选项卡片 │ │  ┌────────────────────────────────────────┐│
│ │ HEX/时间戳 │ │  │        发送区（多行输入）                 ││
│ │ 清除/换行  │ │  └────────────────────────────────────────┘│
│ │ 暂停/日志  │ │  [发送][定时+周期][发送文件]                  │
│ │ 断行模式   │ ├────────────────────────────────────────────┤
│ ├───────────┤ │  多条发送列表（可勾选循环发送）                │
│ │发送选项卡片 │ │  ┌────────────────────────────────────────┐│
│ │ ASCII/HEX │ │  │  [✓] 指令内容        [延迟ms]            ││
│ │ 编码       │ │  │  [✓] AT+RST          [100]              ││
│ ├───────────┤ │  └────────────────────────────────────────┘│
│ │信号线卡片   │ │  [添加][删除][清空][发送选中][循环发送]       │
│ │ DTR/RTS   │ │                                             │
│ │ 复位       │ │                                             │
│ └───────────┘ │                                             │
└───────────────┴────────────────────────────────────────────┘
```

左右用 `QSplitter`（可拖拽调宽，状态持久化），右侧上下用 `QSplitter`（可拖拽调接收/发送区高度）。

## 模块改动清单

| 文件 | 改动 |
|---|---|
| `styles.py` | QSS 全面重写：卡片、按钮层级（primary/danger/ghost）、输入框聚焦态、表格、分割器、接收区两套配色（`#recvGreen` / `#recvBlue`） |
| `main_window.py` | `_build_ui` 重构为左参数 + 右上下；新增 RecvPlainTextEdit、MiniRateChart、多条发送列表、右键菜单、字号/配色切换、统计逻辑 |
| `config.py` | 新增常量：接收区字号、默认配色、多列表设置 key、配色 QSS 片段、速率图参数 |
| `serial_worker.py` | 不改动 |
| `main.py` | 不改动 |

## 新增组件设计

### 1. RecvPlainTextEdit（接收区，main_window.py 内）
`class RecvPlainTextEdit(QPlainTextEdit)`，重写 `wheelEvent`：`Ctrl+滚轮` 调字号（8~24 范围，`QFont.setPointSize`，`setFont`），不改变原有选择文本缩放行为，否则交回 `super()`。字号、配色存 QSettings。

### 2. 接收区配色切换（黑底绿字 / 蓝底白字）
- 接收区不依赖全局 QSS 背景，改用自身 `setStyleSheet`，两套模板（在 config.py 定义常量拼接字号/边框）。
- 绿字：背景 `#0a0f0a`，前景 `#39d353`，等宽字体 `Consolas`（回退 `Microsoft YaHei`）。
- 蓝字：沿用现有 `#181c23` 背景白字。
- 切换入口：右键菜单勾选项；切换时 `setStyleSheet` 重应用，字号不受影响。

### 3. MiniRateChart（自绘速率图，main_window.py 内）
`class MiniRateChart(QWidget)`，纯 `QPainter` 自绘，无第三方依赖：
- 内部 `collections.deque(maxlen=60)` 存 (rx, tx) 速率样本，`rate_timer` 每秒追加并 `update()`。
- `paintEvent`：深色背景圆角矩形，RX 蓝色柱 / TX 橙色柱（半透明），当前值文字显示在接收区头部统计标签。
- 固定高度约 56px，宽约 160px。

### 4. 多条发送列表（XCOM 特色）
`QTableWidget`，3 列：`[✓使用] [指令内容] [延迟ms]`，隐藏行号，`QSS` 样式化。
- 按钮：添加（当前发送区文本入表）、删除选中行、清空、发送选中、循环发送（toggle）。
- 循环发送：收集勾选行（内容 + 延迟），用 `QTimer.singleShot` 链式发送：`发第 i 条 → 延迟 → 发第 i+1 条 → … → 回第 0 条`；按当前发送模式解析（ASCII/HEX 复用 `do_send_text` 的 `parse_hex` 逻辑）。
- 数据持久化：QSettings 存 list of (used, text, delay)；`load_settings`/`save_settings` 扩展。
- 循环中若串口断开或手动停止，终止链。

### 5. 接收区右键菜单
`setContextMenuPolicy(CustomContextMenu)` + `customContextMenuRequested` 弹 `QMenu`：
- 清空显示 / 复制 / 保存接收日志…
- 分隔
- HEX 显示（勾选） / 时间戳（勾选） / 自动换行（勾选）
- 接收配色：黑底绿字 / 蓝底白字（勾选互斥）
- 字体：放大 / 缩小 / 恢复默认

### 6. 统计面板增强
- 状态栏：连接状态圆点（`QLabel` 用 QSS 画 8px 圆：绿=已连接，灰=未连接）+ "已连接 HH:MM:SS"；RX 总字节 / TX 总字节；收/发速率 B/s。
- 接收区头部行：`收 12.3KB 发 1.2KB | 行数 1234 | MiniRateChart`。
- 行数 = `recv_box.blockCount()`，随接收刷新（合并进 `on_data_received`/`update_rate`，不逐包更新）。

### 7. 视觉精修（styles.py 重写）
- 卡片：`QFrame#card`，背景 `#161a22`，`1px solid #2a303b`，圆角 8px；卡片标题 `QLabel#cardTitle` 小字号灰色带字距。
- 按钮层级：`#primary`（蓝 #2568ee，hover 变亮）、`#danger`（红）、默认 ghost（`#222730` 背景）。
- 输入框/下拉：聚焦 `border-color:#4086ff`；下拉项 hover 高亮。
- `QSplitter::handle`：深色细条，hover 高亮。
- `QTableWidget`：深色背景、行 hover、无网格线或细分割线。

## 迁移与保留

- 所有现有信号槽、`SerialWorker` 交互、`find_bar`（Ctrl+F）、快捷键（Ctrl+Enter 发送、Ctrl+L 清空、F5 刷新）、定时发送、发送文件、预设、历史、日志、CRC16、DTR/RTS、复位（QTimer 分步）全部保留，仅重新摆放位置。
- `load_settings`/`save_settings` 保留原 key，新增：`font_size`、`recv_theme`、`multisend_list`、两个 `QSplitter` 状态（`saveState`）。
- 接收区占位符、`setMaximumBlockCount(10000)`、`find()` 查找逻辑不变。

## 实施顺序

1. **styles.py**：重写 QSS（卡片/按钮/输入/表格/分割器/两套接收配色）。
2. **config.py**：新增常量与接收区配色模板。
3. **main_window.py**：
   a. 新增 RecvPlainTextEdit / MiniRateChart 类；
   b. `_build_ui` 重构为左卡片面板 + 右上下 QSplitter 布局；
   c. 多条发送列表（表格 + 增删清/发送选中/循环发送 + 持久化）；
   d. 右键菜单、字号/配色切换、统计显示；
   e. `load_settings`/`save_settings`/`closeEvent` 扩展。
4. 冒烟测试 + 启动验证。

## 验证

- offscreen 冒烟测试：窗口构建、右键菜单动作、字号变更、配色切换、多列表增删/勾选/循环发送序列、速率图 `paintEvent` 不崩、QSplitter 状态持久化。
- `python main.py` 启动无崩溃。
- 手工核对：所有原功能可用（收发、定时、文件、预设、历史、日志、复位、查找、快捷键）；新功能逐项操作。
