# Personal 学习数据的备份与恢复

本文说明如何备份 Personal 模式的学习数据、如何校验备份，以及如何在隔离目录中做恢复演练。

工具：`backend/scripts/personal_backup.py`。逻辑代码在 `backend/services/backup/personal.py`。

## 备份的内容

备份的对象是 `backend/storage/` 下所有的普通文件：

- `learning_library.json` 和 `library_media/`：课程、收听进度和音频；
- `study_bank.json`：Study Bank，以及 SRS 复习状态；
- `speaking_sessions.json`：口语练习和 Topic Mock 的记录；
- `chunk_bank.json`：chunk memory；
- `dictionary.sqlite3` 等 SQLite 文件。

**下面两类文件不在备份范围内**，需要另外处理：

- `DICTIONARY_DB_PATH` 或 `DICTIONARY_FORMS_INDEX_PATH` 指向的、位于 storage 目录**之外**的词典文件。
- `backend/media/` 中的临时媒体。

## 安全性质

- **停服是人工前提。** 必须先由 Owner 手动停止应用，并在命令行中显式传入 `--confirm-app-stopped`。工具还会探测 loopback 端口（IPv4 的 `127.0.0.1` 和 IPv6 的 `::1`），有服务在监听时拒绝执行。但这个探测只是**额外的、尽力而为的拒绝**，端口没有监听**不能证明**应用已经停止（应用可能在别的端口或接口上，也可能正在写入）。
- **只读取真实数据。** 对 storage 只做读取和字节复制，不会直接打开实时的 SQLite 数据库（SQLite 即使以只读方式打开，也可能生成 `-wal` 和 `-shm` 文件）。
- **复制校验。** 每个文件复制完成后，都会从磁盘重新计算 SHA-256，并与复制时读到的字节比较。复制期间如果有源文件的大小或修改时间发生变化，这次备份会被整个丢弃。
- **SQLite。** 先把数据库文件和它的日志文件按字节复制到私有的临时目录，再从这份副本执行 SQLite 的 backup API，生成一个单文件数据库。结果必须通过 `integrity_check`，并且逻辑内容（`iterdump` 的摘要）必须与副本一致。
- **完整性。** 备份先写入 `.partial` 目录，生成 SHA-256 的 `MANIFEST.sha256`，执行 fsync，再重命名为正式目录。已有的备份永远不会被覆盖。任何一步出错，`.partial` 目录都会被删除。
- **恢复先写入独立的 staging 目录，全部通过后才发布。** 恢复时，数据先复制到目标旁边的私有 staging 目录，然后依次检查：
  1. 每个文件都要重新计算 SHA-256，文件集合和哈希值都必须与备份的 MANIFEST 完全相同；
  2. 用应用自己的 store 类重新读取数据，并与备份时生成的摘要比较，包括：
     - 课程和学习进度；
     - SRS 状态；
     - 口语记录及其事件数；
     - chunk memory；
     - SQLite 的完整性检查结果、每张表的行数和逻辑摘要；
  3. 读取完成后再核对一次哈希，确认读取过程没有改动任何文件。

  全部通过之后，才把 staging 目录重命名为目标目录。任何错误都会删除 staging 目录，目标目录**不会**被创建。
- **只使用一次校验的结果。** MANIFEST 只读取一次，`backup.json` 也直接从计算哈希时读到的那份字节中解析。恢复只使用这一次校验得到的条目，不会再读取任何未经校验的版本。
- **路径安全。** 下列路径都会被拒绝：绝对路径、`..` 或 `.` 组成的路径、空的路径段、反斜杠、NUL 和其他控制字符，以及 `storage/` 之外的路径。备份中任何一级路径（包括文件本身）是符号链接时，也会被拒绝。恢复只会写入私有的 staging 目录。
- **原子发布，不覆盖已有目录。** 发布时使用内核级的、不覆盖目标的原子重命名：Linux 上用 `renameat2(RENAME_NOREPLACE)`，macOS 上用 `renamex_np(RENAME_EXCL)`。如果目标在发布的那一刻被别人创建（包括一个空目录），工具会拒绝执行，绝不覆盖。其他平台，以及不支持这种重命名的文件系统，都会失败关闭。
- **恢复目标的限制。** 恢复的目标目录不能是实时 storage 目录，也不能在它里面；目标目录必须是新的，不能已经存在。
- **格式版本。** 备份格式为 `language-ai-personal-backup/2`，`/1` 格式的备份会被拒绝。

## 使用方法

先停止应用，然后在 `backend/` 目录下执行：

```bash
python scripts/personal_backup.py backup --confirm-app-stopped --dest "/Users/<you>/备份/语言网站/personal-data"
```

```bash
python scripts/personal_backup.py verify --backup "/Users/<you>/备份/语言网站/personal-data/personal-backup-<stamp>"
```

```bash
python scripts/personal_backup.py restore --backup "<backup>" --target "/tmp/restore-check-<stamp>"
```

退出码：0 表示成功；2 表示被拒绝或者校验失败。

**把恢复出来的数据放回实时 storage，不由本工具完成。** 这一步需要在停止应用之后，由 Owner 手动操作。操作之前，先对当前的 storage 再做一次备份。

## 测试

`backend/tests/test_personal_backup.py`，只使用合成数据。覆盖的情况：

- BK-1：备份完整，并且可以校验；
- BK-2：恢复后读取到相同的记录和 SRS 状态；
- BK-3：服务运行时拒绝备份；
- BK-4：任何一个字节被篡改，校验都会失败，恢复也会被阻止；
- BK-5：已有的备份不会被覆盖，备份目标也不能在 storage 之内；
- 复制期间 storage 发生变化时，备份被丢弃；
- storage 中有符号链接时拒绝备份；
- SQLite 的 WAL 中尚未写回主文件的内容，也能被备份；
- storage 为空时，也可以正常备份和恢复；
- 命令行工具的完整流程。

此外，每个测试都会检查实时 storage 中所有文件的 SHA-256 和修改时间都没有变化。
