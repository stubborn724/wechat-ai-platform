#!/usr/bin/env sh
# 收敛服务器专属的公开访问地址；敏感配置继续沿用受限权限的根目录 .env。
set -eu

env_file="/opt/wechat-ai-platform/.env"
public_ip="47.94.210.8"

replace_or_append() {
  key="$1"
  value="$2"
  if grep -q "^${key}=" "${env_file}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${env_file}"
  else
    printf '\n%s=%s\n' "${key}" "${value}" >> "${env_file}"
  fi
}

replace_or_append MINIO_PUBLIC_ENDPOINT "http://${public_ip}:9002"
replace_or_append CORS_ORIGINS "http://${public_ip}:5173"
chmod 600 "${env_file}"
