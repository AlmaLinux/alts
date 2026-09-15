-- One-time cleanup for a scheduler.db whose tasks table has grown unbounded.
--
-- Task rows only exist to map a build system task to its Celery task while it
-- can still be cancelled, but nothing used to remove them, so the table grew
-- for the lifetime of the deployment. prune_tasks() in alts/scheduler/db.py
-- keeps it bounded from now on; this script clears an existing backlog.
--
-- Deleting millions of rows runs at roughly 4k rows/s because of the UNIQUE
-- index on task_id, so the table is rebuilt instead: ~2s per million rows.
--
-- Stop alts-scheduler first: the running process holds an open transaction.
-- Keep a copy of the file before running this.
--
--   systemctl stop alts-scheduler
--   cp /srv/alts/scheduler/scheduler.db /srv/alts/scheduler/scheduler.db.bak
--   sqlite3 /srv/alts/scheduler/scheduler.db < scripts/prune_scheduler_db.sql
--   systemctl start alts-scheduler
--
-- Keeps the most recent 200000 rows, matching the task_retention_rows default.

BEGIN;

CREATE TABLE tasks_new (
	id INTEGER NOT NULL,
	task_id VARCHAR,
	bs_task_id VARCHAR,
	callback_href VARCHAR,
	queue_name VARCHAR,
	status VARCHAR,
	task_duration VARCHAR,
	PRIMARY KEY (id),
	UNIQUE (task_id)
);

INSERT INTO tasks_new
    SELECT * FROM tasks
    WHERE id > (SELECT MAX(id) - 200000 FROM tasks);

DROP TABLE tasks;
ALTER TABLE tasks_new RENAME TO tasks;

COMMIT;

VACUUM;
