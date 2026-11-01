-- When the task's last turn ended, so Stop can look at one turn at a time (decision 0034).
ALTER TABLE tasks ADD COLUMN last_stop_at timestamptz;
