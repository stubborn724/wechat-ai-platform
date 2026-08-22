#!/usr/bin/env sh
# 恢复项目向量数据库；连接信息由 PostgreSQL 容器自身环境提供。
set -eu

project_dir="/opt/wechat-ai-platform"
dump_file="${project_dir}/postgres.dump"
container_name="wechat-platform-postgres"

if [ ! -s "${dump_file}" ]; then
  echo "PostgreSQL 迁移文件不存在或为空: ${dump_file}" >&2
  exit 1
fi

docker exec "${container_name}" sh -lc \
  'dropdb --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'

docker exec -i "${container_name}" sh -lc \
  'exec pg_restore --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "${dump_file}"

docker exec "${container_name}" sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT COUNT(*) FROM pg_tables WHERE schemaname = current_schema()"'

rm -f "${dump_file}"
