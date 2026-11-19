# Cloud Phase 1：数据库迁移与运行时角色规程

本文说明 Cloud Phase 1 中，如何安全地执行迁移、建立运行时数据库角色，并在应用启动前做检查。

- 状态：仅用于开发和隔离环境，**不是**生产部署说明。
- 适用范围：迁移 0001–0009，以及 Phase 1.5 的 0014（Library RLS）。
- **0010–0013 是冻结的 B 系列迁移（包括 B1 的媒体清理 worker）的保留编号**：它们永远不会被执行，也不会被其他迁移复用。将来如果恢复 B 系列的工作，必须使用大于 0014 的新编号，并重新审核。

## 1. 涉及的文件

| 文件 | 作用 |
|---|---|
| `backend/migrations/postgresql/PHASE1-MANIFEST.sha256` | 记录 0001–0009 和 0014 每个 up 文件的 SHA-256。0007–0009 的值固定为 P8-05B 分支中文件的内容。顺序规则：0001–0009 必须连续；0009 之后的版本号必须大于 0013，并且严格递增 |
| `backend/services/cloud/schema.py` | 记录每个版本建立的表、需要 FORCE RLS 的表、新增的列和约束，以及运行时角色的最小权限表 |
| `backend/services/cloud/migrations.py` | 只能向前执行的迁移执行器，带有版本记录表 `cloud_schema_migrations` |
| `backend/services/cloud/roles.py` | 创建运行时角色，并按权限表授权 |
| `backend/services/cloud/preflight.py` | 用运行时连接做只读检查 |
| `backend/scripts/cloud_db.py` | 运维人员使用的命令行工具 |

## 2. 安全性质

- **迁移文件**：每个文件都先核对固定的 SHA-256，再执行。文件内容不符、文件缺失、出现嵌套的 `BEGIN` 或 `COMMIT`，或者目标版本是 0010–0013 或不在清单中，都会被拒绝，并且**拒绝发生在任何写入之前**。
- **原子性**：每个版本的 SQL 和它在版本记录表中的那一行，在同一个事务中提交。某个版本失败时，不会留下表，也不会留下版本记录。
- **并发**：使用会话级的 advisory lock。第二个执行器会被直接拒绝，而不是排队等待。
- **不猜测数据库状态**：如果数据库已经有表，但没有版本记录表，执行器会拒绝执行，不会推断这个数据库现在处于哪个版本。
- **只能向前**：没有自动回滚。需要回退时，从备份恢复。
- **凭据**：DSN 只从环境变量读取，命令行工具不打印 DSN 或口令。数据库驱动报错时，只输出错误类型，不输出错误信息（信息中可能包含连接细节）。

## 3. 运行时角色需要防范的风险

Preflight 会拒绝下面任何一种情况：

| 风险 | 检查方法 |
|---|---|
| SUPERUSER 或 BYPASSRLS，会让 RLS 失效 | `pg_roles` |
| CREATEROLE、CREATEDB、REPLICATION | `pg_roles` |
| 拥有表（表的所有者可以关闭 RLS）、拥有 schema、拥有数据库 | `pg_class`、`pg_namespace`、`pg_database` |
| 通过成员关系继承权限（包括 `SET ROLE` 和 `pg_read_all_data` 这类预定义角色） | `pg_auth_members` 中**不能有任何**成员关系；同时要求 `current_user` 等于 `session_user` |
| 可以在 public schema 或数据库中 CREATE | `has_schema_privilege`、`has_database_privilege` |
| 缺少必需权限 | 每项必需权限都必须是**表级**授权（`has_table_privilege`）。只授予部分列的权限（例如单列 SELECT）**不能**满足表级要求 |
| 多出禁止的权限（包括列级授权和授予 PUBLIC 的授权） | 不在权限表中的任何权限，无论是表级授权（`has_table_privilege`）还是任意一列上的授权（`has_any_column_privilege`），都算作越权 |
| 缺少 schema 的 USAGE 权限 | `has_schema_privilege(..., 'USAGE')` |
| RLS policy 漂移 | `pg_policies` 中 public schema 的 policy 集合，必须**完全等于**固定的定义。每张强制 RLS 的表只能有一个 policy，并且要求：PERMISSIVE；roles 为 `{public}`；命令为 ALL；USING 和 WITH CHECK 都是 owner 条件。下面任何一种情况都会被拒绝：`USING (true)` 或 `WITH CHECK (true)`、其他条件表达式、RESTRICTIVE、只对 SELECT 生效、roles 被改动、多出 policy（包括在没有开启 RLS 的表上）、缺少 policy |
| policy 绑定的函数和操作符被同名对象替换（例如 `public.current_setting`，或者 `public` 中一个永远为真的 `=`） | preflight 在固定的 `search_path`（`pg_catalog, pg_temp`）下运行，不受运行时角色自身 search_path 设置的影响。每个 policy 的 USING 和 WITH CHECK，都要在 `pg_policy` 中存储的表达式树上，按 OID 核对实际绑定的对象：`pg_catalog.=(text,text)`、本表的 `owner_user_id` 列，以及 `pg_catalog.current_setting(text,boolean)`，常量的类型也必须一致。另外，`public` schema 中不允许存在任何用户自定义的函数或操作符 |
| 多出了表、视图、序列或 schema（例如冻结的 0010–0013 会建立的对象） | 当前的表集合必须**等于**已执行版本建立的表集合，再加上版本记录表 |
| RLS 被关闭，或者没有 FORCE | `relrowsecurity` 和 `relforcerowsecurity` |
| 版本记录被篡改，或者为空 | 版本记录必须是清单的连续前缀，并且 SHA-256 一致。记录为空时，preflight 和授权都会失败关闭。执行器只在数据库中除了版本记录表之外没有任何表时才继续执行（例如第一个版本执行失败之后），否则拒绝 |

## 4. 操作步骤

所有命令都在 `backend/` 目录下执行。执行前，先用 `read -s` 一类的方式，在**当前 shell** 中设置 DSN 环境变量，以免把 DSN 写进 shell 历史记录。

```bash
python scripts/cloud_db.py plan
```

```bash
python scripts/cloud_db.py apply --target 0014
```

```bash
python scripts/cloud_db.py create-runtime-role --role lap_app
```

为角色开启 LOGIN 并设置口令，需要交互式地进行，口令**不会**经过本工具：

```bash
psql "$CLOUD_ADMIN_DATABASE_URL" -c 'ALTER ROLE lap_app LOGIN'
```

```bash
psql "$CLOUD_ADMIN_DATABASE_URL" -c '\password lap_app'
```

```bash
python scripts/cloud_db.py grant-runtime --role lap_app
```

```bash
python scripts/cloud_db.py preflight --minimum 0014
```

- `--minimum`：只有学习功能时用 0006，需要邮箱登录时用 0009；要求 Library 的数据库层隔离时用 0014。
- 退出码：0 表示成功；2 表示被拒绝或检查失败；3 表示没有设置 DSN 环境变量。

## 5. 不包含的内容

- **G1 中的迁移执行器设计**，例如 checksum 基线和 PG 大版本检查，本规程**没有实现**。G1 仍然只有设计。
- **定时清理任务**：清理过期认证记录的任务，需要一个单独的角色，并且拥有 DELETE 权限。Phase 1 不授予这个权限，也不运行这个任务。
- **Library 的 RLS（0014）**：`cloud_library_items`、`cloud_library_media_assets` 和 `cloud_media_cleanup_outbox` 这三张表都执行了 ENABLE 和 FORCE RLS，policy 与 0002–0004 的形式相同。
  - Library repository 在每个事务开头，用 `set_config('app.current_owner', <已认证的 owner>, true)` 设置一个只在本事务内有效的 owner。
  - 这个设置在事务结束时自动清除，无论是提交、回滚，还是调用途中出错。每次操作都使用新连接，不经过连接池，所以 owner 不会带到下一个事务（测试 LRLS-4、LRLS-10）。
  - 没有设置 owner 时，看不到也写不了任何行（失败关闭）。
  - `cloud_users` 没有 RLS：这张表以 `user_id` 为键，应用只用它来检查 owner 是否已开通。
- **信任边界**：数据库信任应用设置的 `app.current_owner`。运行时角色可以把它设成任意值，所以 RLS 能防住的是查询中漏写或写错了 owner 条件，**防不住**一个已经被攻破、可以执行任意 SQL 的应用。
- **冻结的清理 worker**：它需要跨 owner 处理 outbox。启用 RLS 之后，它必须逐个 owner 设置上下文，或者使用一个单独的、带有 BYPASSRLS、并且不交给应用运行时使用的维护角色。这件事必须在解冻 B1 之前决定。
- **Library 音频不在请求路径中物理删除（Phase 1.5 Task D）**：
  - 删除条目和替换音频时，被退役的对象只会在同一个事务里写入一条 `cloud_media_cleanup_outbox` 记录，状态是 `pending`，`attempt_count` 为 0。请求路径不会 unlink 文件。
  - 元数据删除后，API 不再提供这段音频（返回 404）。但是**字节仍然留在磁盘上**，直到经过批准的清理流程把它移除。
  - 唯一保留的 unlink 是补偿操作：在同一次上传中刚写入的新对象，只有在**确认回滚**时才会被删掉，也就是失败发生在 COMMIT 发出之前。这个对象名唯一，也没有任何元数据或 outbox 记录引用它。
  - **COMMIT 结果未知**：COMMIT 已经发出，但没有收到确认（例如连接中断）。这时事务可能已经提交，READY 元数据可能已经引用了新对象，所以：
    - 新对象**一律保留**，不做补偿删除；
    - 也不向（可能不可用的）数据库查询“是否可以删除”；
    - 调用方收到 `CloudLibraryCommitOutcomeUnknownError`，它属于 `CloudLibraryUnavailableError`，会附带 `item_id` 和 `object_key`；
    - 同时写一条 warning 日志，供日后核对。
    - 如果事务实际上没有提交，这个对象就成为没有被引用的孤儿文件。本阶段只报告这种情况，不自动清理。
  - **加锁顺序**：挂载音频和删除条目，都先锁定条目的父记录（`cloud_library_items`，`FOR UPDATE`），再读取媒体记录。这样第一次挂载时，两个并发事务也能被串行化，被替换的对象一定会写入 outbox。挂载和删除使用相同的加锁顺序，因此不会互相死锁。
  - **尚未满足的条件**：
    1. 隐私删除（用户要求删除后，字节被真正抹除）。
    2. 有期限的最终清理。
    3. 清理角色及其 RLS 处理方式。
    4. 失败重试、重试计数、告警。
    5. 备份和副本中的残留。
    6. 账户注销时的批量清理。
    7. COMMIT 结果未知时可能产生的孤儿文件，如何核对和清理。

    在 B1 解冻并获得批准之前，这些都**不**成立。在那之前，outbox 就是待清理对象的审计清单。

## 6. 学习数据仓储的启动组装（PR-C）

`backend/services/cloud/composition.py` 负责把学习数据的仓储接入应用。它在 Cloud 模式下的**第一个请求**时运行一次：由会话边界（`BrowserSessionBoundary`）在线程池中调用，不会阻塞事件循环。

需要的配置：

| 环境变量 | 说明 |
|---|---|
| `CLOUD_STORAGE_DATABASE_URL` | 运行时角色的 DSN，可以与 `CLOUD_SESSION_DATABASE_URL` 相同 |
| `CLOUD_MEDIA_ROOT` | 课程音频的存放目录。必须是绝对路径，必须已经存在、可以写入，不能是符号链接，也**不能**位于应用的 checkout 目录之内 |
| `CLOUD_STORAGE_MINIMUM_VERSION` | 可选，默认 `0006`。启用邮箱登录时设为 `0009`；要求 Library 的数据库层隔离时设为 `0014` |

失败关闭的规则：

- **两个必填变量都没有设置**：不组装任何仓储。这个状态会被记住，修改配置之后需要重启服务才会生效。
- **配置不完整或不合法、preflight 失败，或者任何一个仓储构造失败**：四个仓储**全部不设置**，所有学习相关的 API 都会返回现有的 503 错误码。失败原因会写入日志，日志中不包含 DSN。这次失败会被记住 30 秒，30 秒之后的请求会重试。
- **已经有仓储被设置过**（例如测试直接注入）：保持原样，不会覆盖。

本模块**不会**启动任何后台任务：没有清理 worker，也没有定时任务。仓储不持有长连接，所以关闭服务时只需要调用 `clear_cloud_storage(app)` 清空引用。

## 7. Phase 1.5 迁移关卡（migration gate）

迁移执行器只应用**经过批准**的版本。每个版本都有两处固定值（pin），两处必须完全一致：

- **清单文件**：`PHASE1-MANIFEST.sha256` 里写着文件名和 SHA-256。
- **代码**：`services/cloud/schema.py` 里也写着同样的文件名和 SHA-256。
  - 0001–0009 记在 `PHASE1_PINNED`。
  - Phase 1.5 新增的版本记在 `PHASE15_ALLOWED`，按应用顺序排列，版本号必须大于 0013。

规则如下：

- **只改清单或只改 SQL 文件，都无法新增或替换迁移。** 新增迁移必须同时修改代码，因此一定要经过代码审查。
- **0010–0013 是冻结 B 系列的保留号，永远不会被应用。** 下面四种情况都会被拒绝：
  - 清单里出现这些版本；
  - 把它们作为迁移目标；
  - 把它们写进 `PHASE15_ALLOWED`；
  - ledger 里出现这些版本（ledger 指迁移执行器记录已应用版本的表）。
- **版本不能跳号，也不能有未知版本。** 清单中的版本必须按批准顺序，从 0001 开始连续排列。
- **0014 之后的每个新版本，都要和它的 schema 描述一起提交。** schema 描述包括表、FORCE RLS、policy 和运行时权限。preflight 会按版本精确核对这些内容，同时核对 ledger 中记录的摘要。
- **迁移失败是原子的。** 一个版本的 SQL 和它的 ledger 行在同一个事务里提交，失败时两者都不会留下。修复原因（例如等对方释放锁）之后，可以直接重试，同一版本只会应用一次。

`PHASE15_ALLOWED` 目前只批准 0014（Library RLS，PR-L）。以后新增的版本，都要和它的 schema 描述一起经过审查。
