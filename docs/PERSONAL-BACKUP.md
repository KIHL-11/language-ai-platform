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

- **只读取真实数据。** 对 storage 只做读取和字节复制。即使是 SQLite 文件，也**不会**直接打开实时数据库（SQLite 即使以只读方式打开，也可能生成 `-wal` 和 `-shm` 文件）。工具先把数据库文件和它的日志文件复制到私有的临时目录，再用 SQLite 的 backup API 从这份副本生成一个**单文件**数据库。
- **服务运行时拒绝备份。** 如果 127.0.0.1:8000（或者 `--port` 指定的端口）有服务在监听，工具会拒绝执行。如果复制期间有任何源文件的大小或修改时间发生变化，这次备份会被整个丢弃。
- **完整性。** 备份先写入 `<dest>/personal-backup-<UTC 时间>.partial/`，对每个文件计算 SHA-256 并写入 `MANIFEST.sha256`。然后对所有文件和目录执行 fsync，最后重命名为正式的目录名。**已有的备份永远不会被覆盖。**
- **拒绝的情况。** 备份目标在 storage 目录之内、storage 中出现符号链接，或者有其他非普通文件时，工具都会拒绝执行。
- **恢复只写入新目录。** 恢复时，工具先校验 MANIFEST，再把备份复制到一个**新的空目录**，然后用应用自己的 store 类读取数据，与备份时生成的摘要逐项比较，包括：
  - 课程的 id 和学习进度；
  - Study Bank 的条目和 **SRS 状态**；
  - 口语记录及其事件数；
  - chunk memory；
  - SQLite 的 `integrity_check` 结果，以及每张表的行数。

  恢复的目标目录不能是实时 storage 目录，也不能在它里面。

## 使用方法

先停止应用，然后在 `backend/` 目录下执行：

```bash
python scripts/personal_backup.py backup --dest "/Users/<you>/备份/语言网站/personal-data"
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
