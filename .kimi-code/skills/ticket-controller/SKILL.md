---
name: ticket-controller
description: '作为总控按波次推进 .scratch/kimico-gateway/issues 下的 ticket：扫描 Blocked by、每波最多 3 个窗口并行、大票独占、单票开始与结束都更新 docs/output/全局Ticket说明.md，并用「树是否变化」决定要不要重跑测试。用于用户要求批量执行 ticket、继续推进里程碑、或问下一张票做什么。'
when_to_use: '用户说开始执行 ticket、按波次推进、继续 milestone、下一张票是什么，或要求控制多窗口并行实现'
---

# Ticket 控制器

控制器是唯一的状态写者。执行窗口（子 agent）只写代码和票内 `## Comments`。

## 硬规则

**起点由控制器判定**：用户只需触发 skill；起始票、波次与并行方式由控制器扫描 `.scratch` 与全局文档后自行计算，不要反问「从哪开始」。只有用户显式指定范围时才覆盖扫描结果。

1. 同一波次最多 **3 个窗口**，一个窗口只做一张票。
2. **大票独占**：当前命名大票为 `13`（主进程接线）与 `16`（验收门禁）。另满足任一条件也独占——改动跨 3 个以上模块、涉及真实 tmux/systemd/NapCat/端口/部署、最终验收类。
3. 只领取 `Blocked by` 全部为 `done` 的票；绝不跳 blocker。
4. 每张票**开始和结束各更新一次** `docs/output/全局Ticket说明.md`；窗口不得编辑该文件。首次使用时该文件不存在，由控制器在第一波开始前创建。
5. 验收未过不标 `done`、不开下一波。
6. 全量测试只在波次边界跑；树没变就不重跑。
7. 状态必须落在文件里，不靠记忆。上下文被压缩后，从文件恢复现场。
8. **Step 0 门禁**：`docs/output/spike/kimico-capture.md` 不存在时，不开放任何实现窗口。`docs/prd/40-build-order.md` 规定真实 kimico TUI 门禁先于编码，没有该产物不得进入 Step 1。

## 事实源

| 用途                 | 文件                                                                                                              |
| -------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 依赖与构建顺序       | `.scratch/kimico-gateway/issues/*.md`、`docs/prd/40-build-order.md`                                               |
| 完成度与执行日志     | `docs/output/全局Ticket说明.md`                                                                                   |
| 单票状态与验收项     | `.scratch/kimico-gateway/issues/<NN>-<slug>.md`                                                                   |
| Spec 与 seam         | `.scratch/01-runtime-foundation/spec.md` … `.scratch/05-orchestration-deployment/spec.md`、`.scratch/README.md`   |
| 票的拆分约定         | `docs/agents/issue-tracker.md`                                                                                    |
| 领域语义             | `GLOSSARY.md`、`docs/agents/domain.md`                                                                            |
| 模块契约             | `docs/prd/10-mod-config.md` … `docs/prd/18-mod-main.md`、`docs/prd/20-acceptance.md`、`docs/prd/30-deploy.md`     |

票状态用 `ready-for-agent` → `in-progress` → `done`；无法继续用 `blocked`。完成度以票内 `**Status:**` 为准，全局文档跟随回写；构建顺序以 `docs/prd/40-build-order.md` 为准，不改写它。

## 0. 会话开场（preflight）

```bash
rg -n '^(# [0-9]|\*\*(Blocked by|Status):\*\*)' .scratch/kimico-gateway/issues/*.md
git status --short
git branch --show-current
git worktree list
date '+%F %T'
test -f docs/output/spike/kimico-capture.md && echo 'Step 0 gate: ok' || echo 'Step 0 gate: missing'
```

按顺序处理：

1. 对齐状态：票内 `**Status:**` 是唯一事实源；`docs/output/全局Ticket说明.md` 存在时回写，不存在则在本波开始前创建。
2. **脏工作区**：`.scratch/` 若尚未纳入 git，先做一次 checkpoint commit（只提交票与 spec，不改写已有历史）；否则 worktree 中看不到票。已标 `done` 但改动未提交的票，先落 checkpoint，再开新票。
3. 环境：`python3 --version`。`python3 -m pytest` 不可用时，为开发测试建 `.venv` 并安装 pytest；运行时保持零第三方依赖。真实 tmux/systemd/NapCat 相关验收按票内说明准备。
4. **Step 0 门禁**：缺少 `docs/output/spike/kimico-capture.md` 时停止开窗，向用户报告需要先完成真实 TUI 门禁；不要用 fake-kimico 结果替代。
5. 首次使用 worktree 时，把 `.worktrees/` 与 `.venv/` 加进 `.gitignore`。
6. 若全局文档「进行中」块或票内 `**Status:** in-progress` 有开始无结束，说明上次中断；先恢复那张票，不要开新票。

## 1. 计算波次

1. 候选集 = `Status != done` 且 `Blocked by` 中每个依赖编号对应的票都是 `done` 的票。`Blocked by` 括号内是标题，只取开头的两位编号。
2. 按全局文档的 W 编号、再按 `NN` 排序。
3. 逐张尝试放入本波，命中任一条件即停止：
   - 已放满 3 个窗口；
   - 下一张是大票 → 本波只跑它（本波已有票则顺延到下一波独占）；
   - 与已入选票冲突（见下）。
4. 本波全部落地并跑完波次验收后，回第 1 步。

**冲突扫描**（入选前做，命中即串行）：

- 都改同一个模块文件：`kimico_gateway/config.py`、`state.py`、`session.py`、`onebot.py`、`router.py`、`input.py`、`capture.py`、`mirror.py`、`__main__.py`；
- 都改共享契约：`router.py` 的固定文案表、OneBot 事件/发送接口、状态字段、日志格式、退出码；
- 都改 `tests/conftest.py` 或同一测试夹具；
- 都改同一个验收脚本或部署文件。

读票里的「What to build」与验收清单，再用 `git grep` 确认契约文件。冲突时先抽一个最小前置改动冻结契约，再放开并行。

## 2. 窗口隔离

每张票一个 worktree + 分支：

```bash
git worktree add .worktrees/<NN> -b feat/<NN>-<slug> main
cd .worktrees/<NN>
```

- 每个 worktree 有独立的工作目录；测试优先复用主仓库 `.venv/bin/python`，没有则在本 worktree 建 `.venv`。
- 纯单元测试可并行；会碰真实 tmux server、固定端口、systemd 或共享状态目录的测试**必须串行**。窗口跑这类测试前申请锁：

```bash
flock /tmp/kimico-gateway-integration.lock bash -c '<测试命令>'
```

- 触及 `__main__.py` 或共享契约的票先落地，其他窗口 rebase 后再跑受影响测试。
- 波次结束后移除 worktree：`git worktree remove .worktrees/<NN>`。

## 3. 窗口任务书

每个窗口一个 `coder` 子 agent，提示词必须自包含：

```text
你是窗口 <Wn-k>，只负责 ticket <NN>（<标题>）。

票：<绝对路径>
Spec：<绝对路径>
PRD：<相关模块文档与章节>
worktree：<绝对路径>（已创建，分支 feat/<NN>-<slug>）
基线：main @ <sha>

## 验收标准
<票内清单原文>

## 文件面
允许修改：<目录/文件>
禁止修改：docs/output/全局Ticket说明.md、.scratch/**（本票的 ## Comments 除外）、其他 ticket 文件、其他分支、docs/prd/**

## 规则
- 先读票、spec、相关 PRD，不扩大范围
- 先按票与 PRD 测试清单写失败测试，再实现
- 改动全部提交到本分支，commit 信息 `feat(<scope>): <摘要> (<NN>)`
- 自测：`python3 -m pytest <相关测试文件>`；改动跨模块时跑全量 `python3 -m pytest`
- 涉及真实 tmux/端口/systemd 的测试先拿 flock 锁
- 不引入运行时第三方依赖；pytest 只用于开发测试
- 不得删改测试来绕过失败；不得跳过验收项
- 在票的 ## Comments 追加：commit SHA、执行的命令、结果、未完成项与假设

## 返回
commit SHA、改动文件、测试命令与结果、未完成项/假设。
```

失败续做时用 `resume` 恢复同一窗口，附上失败输出与 diff，不要新开 agent。

## 4. 验收分层

> 重跑是为了验证「合并」这个动作，不是验证「实现」这个动作。

| 层级     | 谁跑   | 内容                                                                                   | 频率      |
| -------- | ------ | -------------------------------------------------------------------------------------- | --------- |
| 每票     | 窗口   | `python3 -m pytest` 相关文件；票点名的真实 tmux/systemd 验收                           | 每票 1 次 |
| 落地     | 控制器 | 树比对；树变了才跑受影响范围                                                           | 每票 1 次 |
| 波次收尾 | 控制器 | `python3 -m pytest`；必要时 `python3 -m compileall -q kimico_gateway` 与 Step 0 回归    | 每波 1 次 |

**树比对**（落地前）：

```bash
TESTED=<窗口返回的 commit>
git rebase main
git diff --stat "$TESTED" HEAD
```

- 输出为空 → 被测树就是落地树，直接采纳窗口结果，不重跑。
- 输出非空 → 只跑受影响范围：改动模块对应的 `python3 -m pytest <测试文件>` + 该票验收项；不跑全量、不跑真实 systemd 验收。
- 合并方式用 `git merge --ff-only`；不能 ff 就先 rebase，再按上面判断。

**控制器抽查**（不重跑也要做，很快）：

- `git diff --stat main...HEAD` 是否落在票声明的文件面内，有没有夹带全局文档、其他票、构建产物、`.venv`；
- 票内清单是否逐条有对应实现与断言，测试名能否对上验收项；
- 有没有 `.skip` / `xfail` / `pytest.skip`、被削弱的断言、为过测试而改期望值；
- 有没有引入运行时第三方依赖，或改写 `docs/prd/**` 的事实来源。

**波次收尾**在 main 上跑一次全量。改了部署物时额外按 `docs/prd/30-deploy.md` 检查：

```bash
python3 -m pytest
python3 -m compileall -q kimico_gateway
```

真实 kimico 的 Step 0 回归按 `docs/prd/40-build-order.md` 的通过标准判断；不过则停止开新波。

## 5. 落地顺序

1. 触及 `__main__.py` 或共享契约的票最先合并，其余窗口 rebase 后重新验证受影响范围。
2. 其余按票编号顺序合并，每张合并后跑一次受影响范围。
3. 每张票落地后重算候选集，但**整波结束才开下一波**。
4. 全量红 → 停止开新波，定位是哪个票引入；回滚该次合并或退回原窗口。

## 6. 文档更新

开始和结束都只由控制器写。时间用 `date '+%F %T'`。

**开始**：

1. 票顶部 `**Status:** ready-for-agent` → `in-progress`。
2. `docs/output/全局Ticket说明.md`（不存在则先创建）：
   - 「更新时间」；
   - 「当前结论」写明正在执行哪张、窗口号、分支、下一张；
   - 波次表该行标「进行中」；
   - 所属 spec/里程碑表格的状态列改「进行中」；
   - 「开工顺序」按当前 frontier 重写；
   - 「执行日志」追加一行，并在「进行中」块写入该票。
3. 再开窗口。

**结束**：

1. 票内勾选全部清单，`**Status:** done`，`## Comments` 追加控制器的验收命令与结果、合并 commit。
2. 全局文档：结论、波次表（标完成）、spec/里程碑表格（done）、开工顺序、执行日志「结束」行。
3. 「进行中」块：开始写入，结束删除；上下文压缩后靠它恢复。
4. 不维护额外的 `MILESTONES.md`；构建顺序以 `docs/prd/40-build-order.md` 为准，不改写它。

全局文档最小结构：

```markdown
# 全局 Ticket 说明

更新时间：YYYY-MM-DD HH:MM:SS

## 1. 当前结论
## 2. 波次表
| 波次 | 窗口 | Ticket | 标题 | 状态 | 分支/Commit | 验收 |
## 3. Spec/里程碑
| Spec | 范围 | 状态 |
## 4. 开工顺序
## 5. 执行日志
| 时间 | 波次 | 窗口 | Ticket | 事件 | 分支/Commit | 验收 |
### 5.1 进行中
- 无
```

执行日志格式：

```markdown
| 时间             | 波次 | 窗口 | Ticket | 事件 | 分支/Commit              | 验收                                      |
| ---------------- | ---- | ---- | ------ | ---- | ------------------------ | ----------------------------------------- |
| 2026-11-02 10:00 | W1   | W1-1 | 01     | 开始 | feat/01-walking-skeleton | -                                         |
| 2026-11-02 12:00 | W1   | W1-1 | 01     | 结束 | abc1234                  | pytest 相关测试通过；波次全量待跑         |
```

## 7. 失败与阻塞

- 验收红：退回原窗口续做，附失败输出。连续两轮不过，控制器接管或标 `blocked`。
- 规格歧义：按保守方案继续，把假设写进票的 `## Comments` 和全局文档；不要停在原地等答复。
- 窗口超时/失联：先看 worktree 的 `git log`、`git status`，再决定 resume 还是丢弃重开。
- 依赖未完成：不开下游票；确需跳票，先改 `Blocked by` 并在 `## Comments` 说明。
- Step 0 门禁未过：停止所有实现窗口，不用 fake-kimico 结果替代真实 TUI 结论。
- 任何情况下都不通过删除/跳过测试来变绿。

## 8. 波次报告

每波结束给用户一段短报告：

```text
W<n>：<票清单>
- 落地：<NN> <commit>
- 验收：窗口自测结果 + 控制器落地检查 + 波次全量结果
- 文档：全局Ticket说明.md 已更新
- 下一波：<候选票与并行/独占判断>
```

最终回复要能独立成立，不依赖子 agent 的原始输出。

## 9. 上下文压缩后的恢复

按顺序重建现场，不要相信记忆：

```bash
sed -n '/## 5. 执行日志/,$p' docs/output/全局Ticket说明.md
rg -n '^\*\*Status:\*\* (in-progress|blocked)' .scratch/kimico-gateway/issues/*.md
git worktree list
git branch --list 'feat/*'
git status --short
```

有「开始」无「结束」的票就是当前窗口；worktree 还在就从那里续，worktree 没了就从票的 `## Comments` 最后一条 commit 重建。

## 10. 不要做

- 不把大票和任何票放同一波。
- 不一个窗口塞两张票，不并发超过 3 个窗口。
- 不让窗口写全局文档、其他票或 `docs/prd/**`。
- 不在测试红、清单未勾、Step 0 门禁未过时标 `done`。
- 不为「保险」重跑树没变的票的全量测试。
- 不在波次验收未过时开下一波。
