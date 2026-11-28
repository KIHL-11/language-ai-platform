# Cloud PostgreSQL：备份、校验、恢复与 0014 回退规程

本文说明如何备份 Cloud 的 PostgreSQL 数据库和 Library 媒体文件、如何校验备份、如何恢复到一个**新的空数据库**，以及 0014（Library RLS）的回退方式。

- **状态**：用于开发环境、隔离环境和演练。**不是**生产部署说明。在生产环境执行任何一步，都需要 Owner 单独授权。
- **工具**：
  - `backend/scripts/cloud_backup.py`：命令行工具；
  - `backend/services/cloud/backup.py`：逻辑代码；
  - `backend/services/cloud/backup_drill.py`：自动化演练。
- **前提**：PostgreSQL 16。`pg_dump` 和 `pg_restore` 的**主版本必须与服务器相同**，否则工具会拒绝执行。可以用 `CLOUD_PG_BIN` 指定这两个程序所在的目录。

## 1. 为什么不能直接用 `pg_dump`

0002–0004 的表（0014 之后还包括 3 张 Library 表）开启了 `ENABLE` 和 `FORCE ROW LEVEL SECURITY`。FORCE 对表的 owner，也就是迁移角色，**同样生效**。

| 做法 | 结果 |
|---|---|
| 用迁移角色执行 `pg_dump` | **失败**：`query would be affected by row-level security policy for table "cloud_ai_chunk_batches"` |
| 用迁移角色执行 `pg_dump --enable-row-security` | **exit 0，但是导出的数据不全**。没有设置 owner 上下文时，RLS 表中一行都看不到。演练中，0009 时丢失了 `cloud_study_items`；0014 之后，又丢失了 `cloud_library_items` 和 `cloud_library_media_assets`。恢复出来的数据库看起来完整，**实际上没有用户数据** |
| 用运行时角色执行 | 不可行：运行时角色是 NOBYPASSRLS，而且对 `cloud_study_media_provenance`、`cloud_topic_targets` 没有 SELECT 权限 |
| **用专门的备份角色**（BYPASSRLS，只读），通过本工具执行 | 能导出所有行。每张表的行数和内容摘要都写入 manifest，恢复时逐表核对 |

**规定：**
- 永远不要使用 `--enable-row-security`；
- 不要用迁移角色或运行时角色做备份；
- 本工具**从不**传递这个参数，并且会剔除 `PGOPTIONS` 等可能打开 `row_security` 的环境变量；连接参数中如果带有 `options`，工具会拒绝执行。

## 2. 三种角色，以及它们必须分开

| 角色 | 用途 | 必须满足 | 工具中的检查 |
|---|---|---|---|
| 迁移角色（table owner，例如 `lap_owner`） | `cloud_db.py apply`、`grant-runtime` | 拥有全部表；不给应用使用 | `check-roles`：所有表只有这一个 owner |
| 运行时角色（例如 `lap_app`） | 应用连接 | 不能是 SUPERUSER；不能有 BYPASSRLS、CREATEROLE、CREATEDB、REPLICATION；没有任何成员关系；不拥有任何对象；只有权限表中的权限 | `cloud_db.py preflight`（已有），`check-roles` |
| **备份角色**（例如 `lap_backup`） | `cloud_backup.py backup` | **BYPASSRLS**；不能是 SUPERUSER；不能有 CREATEROLE、CREATEDB、REPLICATION；INHERIT；成员关系**只允许** `pg_read_all_data`；不拥有任何对象；对所有表都**没有** INSERT、UPDATE、DELETE、TRUNCATE、REFERENCES、TRIGGER 权限；没有 CREATE 权限；能 SELECT 所有表 | 每次备份之前，都用当前连接重新检查。任何一项不满足就拒绝，不会写出任何文件 |
| 恢复角色（超级用户，或者托管平台的管理员） | `cloud_backup.py restore`，**只用于新建的空数据库** | 能按原来的 owner 和授权重建对象；能绕过 RLS，否则既无法写入 FORCE RLS 的表，也无法校验 | 不能绕过 RLS 的话，工具拒绝执行 |

**备份角色的凭据**：
- **不能**交给应用，也不能放进任何 `VITE_*` 变量；
- 只在备份时使用；
- 它有 BYPASSRLS，可以读取所有用户的数据，要按照最高敏感度保管。

建立备份角色（需要超级用户，或者托管平台中能创建 BYPASSRLS 角色的管理员）：

```bash
python scripts/cloud_backup.py create-backup-role --role lap_backup
```

```bash
psql "$CLOUD_PRIVILEGED_DATABASE_URL" -c 'ALTER ROLE lap_backup LOGIN'
```

```bash
psql "$CLOUD_PRIVILEGED_DATABASE_URL" -c '\password lap_backup'
```

```bash
python scripts/cloud_backup.py check-roles --runtime-role lap_app --backup-role lap_backup
```

**托管平台的限制**：PostgreSQL 16 中，只有超级用户能创建 BYPASSRLS 角色。很多托管平台不提供真正的超级用户。选定平台之前，必须先确认以下两点之一：
- 平台的管理员角色可以创建 BYPASSRLS 角色；
- 平台提供的快照或 PITR 备份覆盖整个实例（快照是物理备份，不受 RLS 影响）。

如果两者都没有，这个平台就不满足 G-BKP。

## 3. 备份

1. **停止写入**：停止应用，或者进入维护模式。只有在没有任何写入时，数据库与媒体文件才是一致的。工具要求显式传入 `--confirm-writes-frozen`。另外，在复制媒体的前后各列一次目录，有任何变化就丢弃这次备份。
2. 执行：

```bash
python scripts/cloud_backup.py backup --media-root /srv/cloud-media --dest /backups --confirm-writes-frozen
```

工具依次执行：
- 检查备份角色（§2）；
- 检查 `pg_dump` 的主版本与服务器相同；
- 开启一个 `REPEATABLE READ READ ONLY` 事务，导出 snapshot。在**同一个 snapshot** 中读取：
  - 版本记录；
  - 每张表的行数，以及与行顺序无关的 SHA-256 内容摘要；
  - Library 的媒体记录和 outbox；
- 用 `pg_dump --snapshot=<同一个 snapshot> --format=custom` 导出。数据库的导出与上面的摘要描述的是同一时刻；
- 复制整个媒体目录，复制后重新计算哈希。每个 READY 媒体记录都必须有对应的文件，大小和 SHA-256 也必须一致，否则拒绝；
- outbox 中等待清理的对象（#97 保留下来的字节）和没有被引用的对象（COMMIT 结果未知时留下的孤儿）也会一起备份，并在 manifest 中分类计数；
- 写入 `.partial` 目录，生成 `manifest.json` 和 `MANIFEST.sha256`，执行 fsync，再用不覆盖目标的方式改名发布。

备份目录的结构：

```text
cloud-backup-<UTC 时间>-<随机>/
  database.dump
  media/<object_key>
  manifest.json
  MANIFEST.sha256
```

**备份中包含所有用户的数据**，保存方式至少要做到：加密存储、限制访问、异地保存一份，并且不要放进仓库。工具会拒绝把备份写进仓库目录或媒体目录。

## 4. 校验

```bash
python scripts/cloud_backup.py verify --backup /backups/cloud-backup-<...>
```

检查内容：
- `MANIFEST.sha256` 中列出的文件集合与实际完全一致，每个文件的哈希都正确，并且没有符号链接；
- manifest 的格式；
- dump 的 SHA-256；
- 每个媒体对象的大小和哈希；
- `pg_restore --list` 可以读取 dump，并且其中的表数据条目与 manifest 一致。

## 5. 恢复（只恢复到新的空数据库）

```bash
createdb lap_restore_YYYYMMDD
```

```bash
python scripts/cloud_backup.py restore --backup /backups/cloud-backup-<...> --target-media-root /srv/restore-media --live-media-root /srv/cloud-media
```

工具依次执行：
- 先执行完整的校验（§4）；
- 拒绝以下情况：
  - 目标数据库中 public schema 已经有任何表；
  - 目标媒体目录不是空的；
  - 目标媒体目录与线上媒体目录有包含关系；
  - 恢复角色不能绕过 RLS；
  - 服务器的主版本与备份不同；
- 执行 `pg_restore --exit-on-error --single-transaction`；
- 把媒体先复制到 staging 目录，逐个校验后再改名；
- **逐表重新计算行数和内容摘要，与 manifest 对比**，任何一张表不同都会失败，并给出表名；版本记录也必须完全一致；所有 READY 媒体记录都必须在新的媒体目录中找到对应文件，大小和哈希一致。

恢复之后、启动应用之前：

```bash
CLOUD_RUNTIME_DATABASE_URL=... python scripts/cloud_db.py preflight --minimum 0009
```

使用 0014 时，`--minimum` 改为 `0014`。然后把应用的 `CLOUD_STORAGE_DATABASE_URL` 和 `CLOUD_MEDIA_ROOT` 指向恢复出来的数据库和目录，**不要覆盖原来的数据库**。

### 数据损失的边界（RPO）

这是逻辑备份，**恢复点就是备份时刻**。备份之后发生的所有写入，包括数据库和媒体文件，恢复之后都会丢失。演练中会专门验证这一点：在备份 B0 之后写入一个条目，恢复 B0 之后，这个条目不存在。
- 需要更小的 RPO 时，要用托管平台的 PITR 或 WAL 归档。本工具不提供这些功能。
- 媒体文件的备份频率，必须与数据库备份保持一致。

### 恢复耗时（RTO）

演练会输出每一步的耗时。在 CI 的一次性 PostgreSQL 16 中，用合成数据测得：

| 步骤 | 耗时 |
|---|---|
| 备份 | 约 0.15–0.2 秒 |
| 恢复数据库 | 约 0.15 秒 |
| 恢复媒体 | 约 0.005 秒 |
| 校验 | 约 0.02 秒 |

这是**合成数据的下限**，不能代表生产环境。生产环境的 RTO 必须在实际托管平台上，用接近实际的数据量单独测量。

## 6. 0014 的回退策略

**前提**：包含 #96 和 #97 的代码在 0009 和 0014 上都能运行（兼容性测试）。所以回退 0014，并不需要先回退代码。

| 选项 | 做法 | 数据损失 | 演练结果 |
|---|---|---|---|
| **R0：前向修复（首选）** | 保留 0014，修复代码或配置 | 无 | — |
| **R1：从迁移前的备份恢复** | 迁移前的备份 B0 → 恢复到新的空数据库 → `preflight --minimum 0009` → 应用改为连接新的数据库，`CLOUD_STORAGE_MINIMUM_VERSION=0009` | **B0 之后的所有写入** | 通过：数据、媒体、版本记录一致；应用能读取；不同 owner 互相隔离；B0 之后的写入不存在；之后可以重新迁移到 0014 |
| **R2：down 文件加版本记录修正（需要 Owner 单独批准）** | 维护模式下，用迁移角色在**一个事务中**执行：0014 down 文件的语句（去掉文件里自带的 `BEGIN` 和 `COMMIT`），然后 `DELETE FROM cloud_schema_migrations WHERE version = '0014'` | 无 | 通过：`preflight --minimum 0009` 正常；除版本记录外，每张表的行数不变；`plan` 再次显示 0014 待执行 |
| **禁止：只执行 down 文件** | — | — | 版本记录中仍然有 0014，`plan` 认为没有待执行的版本；`preflight` 无论用 0009 还是 0014 都会失败，应用无法启动（M-1） |

- R2 是手工修改版本记录，不在执行器中实现，执行器仍然只能向前；
- 执行 R1 或 R2 之前，都必须先做一次**新的**备份，并通过 §4 的校验。

## 7. 自动化演练

```bash
CLOUD_DRILL_DISPOSABLE=yes CLOUD_DRILL_SUPERUSER_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/postgres python scripts/cloud_backup.py drill
```

- **运行前提**：只能在 loopback 地址或本地 socket 上运行，并且必须设置 `CLOUD_DRILL_DISPOSABLE=yes`。
- 演练创建的数据库、角色和目录都使用随机的 `lapdrill_` 名称，**结束后全部删除**。

演练的步骤：
1. **源库到 0009**：建立迁移、运行时和备份三种角色，并做 `check-roles` 检查。
2. **合成数据**：通过真实的 repository，为两个 owner 写入 Library 条目、音频和 Study Bank 条目。
3. **反向验证**：
   - 运行时角色、迁移角色作为备份角色，都会被拒绝；
   - 没有 `--confirm-writes-frozen` 时会被拒绝；
   - 迁移角色直接执行 `pg_dump` 会失败（H-1）；
   - 用 `--enable-row-security` 导出的 dump，会被恢复时的校验拒绝。这一步在 0009 和 0014 各做一次。
4. **备份 B0**，并校验。
5. **迁移**到当前代码批准的最高版本，执行 preflight，然后在 B0 之后再写入一条数据。
6. **恢复 B0**，然后检查：
   - preflight 用 0009 正常，用最高版本被拒绝；
   - 应用读取正常：每个 owner 只能看到自己的条目，音频字节完整，Study Bank 正常，跨 owner 的读取被拒绝；
   - B0 之后写入的那条数据不存在。
7. **重新迁移**恢复出来的数据库，然后检查：preflight 正常，应用功能正常，没有设置 owner 时看不到任何 Library 行。
8. **迁移之后的备份 B1**，以及它的恢复。
9. **0014 的回退**：验证 M-1，以及 R2（只在有 0014 时执行）。

## 8. 不包含的内容

- PITR、WAL 归档和定时备份；
- 备份的加密和异地复制，由托管平台或运维流程负责；
- 生产环境的实际 RTO 测量；
- 媒体文件的物理删除、隐私擦除，以及清理 worker，这些仍然冻结；
- 在生产数据库上执行任何一步，都需要 Owner 单独授权。
