#!/usr/bin/env sh
# 将迁移包导入服务器的项目 MySQL 容器。
# 密码和数据库名只从容器环境变量读取，避免多层 SSH/Shell 转义造成变量提前展开。
set -eu

project_dir="/opt/wechat-ai-platform"
dump_file="${project_dir}/mysql.sql"
container_name="wechat-platform-mysql"

if [ ! -s "${dump_file}" ]; then
  echo "MySQL 迁移文件不存在或为空: ${dump_file}" >&2
  exit 1
fi

docker exec -i "${container_name}" sh -lc \
  'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' < "${dump_file}"

docker exec "${container_name}" sh -lc \
  'mysql -N -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=\"$MYSQL_DATABASE\""'

rm -f "${dump_file}"
